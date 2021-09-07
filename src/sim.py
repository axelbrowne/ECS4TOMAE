from agent import SoloAgent, NaiveAgent, SensitiveAgent, ExtraSensitiveAgent, AdjustAgent
from world import World
from data import DataBank
from assignment_models import ActionModel, DiscreteModel, RandomModel
from util import printProgressBar
from environment import Environment
import plotly.graph_objs as go
import time
from numpy import random
import multiprocessing as mp
from pandas import DataFrame
from os import mkdir
from json import dump
from itertools import cycle

class Sim:
  def __init__(self, environment_dicts, policy, div_node_conf, asr, T, MC_sims, EG_epsilon=0, EF_rand_trials=0, ED_cooling_rate=0, is_community=False, rand_envs=False, env_mutation_chance=0, show=True, save=False, seed=None):
    self.seed = int(random.rand() * 2**32 - 1) if seed is None else seed
    self.rng = random.default_rng(self.seed)
    self.start_time = time.time()
    self.rand_envs = rand_envs
    self.emc = env_mutation_chance
    self.environments = [Environment(env_dict) for env_dict in environment_dicts]
    self.num_agents = len(self.environments)
    self.assignments = {
      "policy": policy,
      "div_node_conf": div_node_conf,
      "asr": asr,
      "epsilon": EG_epsilon,
      "rand_trials": EF_rand_trials,
      "cooling_rate": ED_cooling_rate,
    }
    if isinstance(asr, str):
      if asr != "EG":
        del self.assignments["epsilon"]
      if asr != "EF":
        del self.assignments["rand_trials"]
      if asr != "ED":
        del self.assignments["cooling_rate"]
    elif isinstance(asr, (tuple, list, set)):
      if "EG" not in asr:
        del self.assignments["epsilon"]
      if "EF" not in asr:
        del self.assignments["rand_trials"]
      if "ED" not in asr:
        del self.assignments["cooling_rate"]
    self.ind_var = self.get_ind_var()
    self.T = T
    self.MC_sims = MC_sims
    self.num_threads = mp.cpu_count()
    self.ass_perms = self.get_assignment_permutations()
    self.is_community = is_community
    self.show = show
    self.save = save
    self.saved_data = DataFrame()
    self.values = self.get_values(locals())
    self.domains = self.environments[0].get_domains()
    self.act_var = self.environments[0].get_act_var()
    self.rew_var = self.environments[0].get_rew_var()
    
  def get_ind_var(self):
    ind_var = None
    for var, assignment in self.assignments.items():
      if isinstance(assignment, (list, tuple, set)):
        assert ind_var is None
        ind_var = var
    return ind_var
  
  def get_assignment_permutations(self):
    if self.ind_var is None:
      return [self.assignments]
    permutations = []
    for ind_var_assignment in self.assignments[self.ind_var]:
      permutation = dict(self.assignments)
      permutation[self.ind_var] = ind_var_assignment
      permutations.append(permutation)
    return permutations
      
  def agent_maker(self, rng, name, environment, databank, assignments):
    policy = assignments.pop("policy")
    if policy == "Solo":
      return SoloAgent(rng, name, environment, databank, **assignments)
    elif policy == "Naive":
      return NaiveAgent(rng, name, environment, databank, **assignments)
    elif policy == "Sensitive":
      return SensitiveAgent(rng, name, environment, databank, **assignments)
    elif policy == "ExtraSensitive":
      return ExtraSensitiveAgent(rng, name, environment, databank, **assignments)
    elif policy == "Adjust":
      return AdjustAgent(rng, name, environment, databank, **assignments)
    else:
      raise ValueError("Policy type %s is not supported." % policy)
      
  def environment_generator(self, rng):
    template = {node: model.randomize(rng) for node, model in self.environments[0]._assignment.items()}
    base = Environment(template, self.rew_var)
    rand_var = rng.choice(list(self.environments[0].get_non_act_vars()))
    for _ in range(self.num_agents):
      if rng.random() < self.emc:
        randomized = dict(template)
        randomized[rand_var] = randomized[rand_var].randomize(rng)
        yield Environment(randomized, self.rew_var)
        continue
      yield base
      
  def world_generator(self, rng):
    assignments = [dict(ass) for ass in self.ass_perms for _ in range(self.num_agents)]
    if not self.is_community:
      rng.shuffle(assignments)
    envs = cycle(self.environment_generator(rng)) if self.rand_envs else cycle(self.environments)
    for _ in range(len(self.ass_perms)):
      databank = None
      databank = DataBank(self.domains, self.act_var, self.rew_var, data={}, divergence={})
      agents = [self.agent_maker(rng, str(i), next(envs), databank, assignments.pop()) for i in range(self.num_agents)]
      yield World(agents, self.T, self.is_community)
  
  def multithreaded_sim(self):
    jobs = []
    results = mp.Manager().list([None] * self.num_threads)
    for i in range(self.num_threads):
      job = mp.Process(target=self.simulate, args=(results, i))
      jobs.append(job)
      job.start()
    [job.join() for job in jobs]
    return results
  
  def simulate(self, results, index):
    rng = random.default_rng(self.seed - index)
    process_result = [{},{}]
    for i in range(self.MC_sims):
      for j, world in enumerate(self.world_generator(rng)):
        for k in range(self.T):
          world.run_episode(k)
          printProgressBar(i*len(self.ass_perms)+j+(k+1)/self.T, self.MC_sims * len(self.ass_perms))
        self.update_process_result(process_result, world)
    results[index] = process_result
  
  def update_process_result(self, process_result, world):
    raw = [world.pseudo_cum_regret, world.optimal_action]
    for i in range(len(raw)):
      if i == 0 and self.is_community:
        ind_var = world.agents[0].get_ind_var_value(self.ind_var)
        data = [sum(d) for d in zip(*raw[i].values())]
        if ind_var not in process_result[i]:
          process_result[i][ind_var] = [data]
          continue
        process_result[i][ind_var].append(data)
        continue
      for agent, data in raw[i].items():
        ind_var = agent.get_ind_var_value(self.ind_var)
        if ind_var not in process_result[i]:
          process_result[i][ind_var] = [data]
          continue
        process_result[i][ind_var].append(data)
    return
  
  def combine_results(self, process_results):
    results = [{},{}]
    for pr in process_results:
      for i in range(len(results)):
        for ind_var, res in pr[i].items():
          if ind_var not in results[i]:
            results[i][ind_var] = res
            continue
          results[i][ind_var].extend(res)
    return results

  def get_plot(self, results, plot_title, yaxis_title):
    figure = []
    x = list(range(self.T))
    for i, ind_var in enumerate(sorted(results)):
      line_hue = str(int(360 * (i / len(results))))
      df = DataFrame(results[ind_var])
      if yaxis_title == "Cumulative Pseudo Regret":
        self.saved_data.insert(0, str(ind_var), df.iloc[:,-1])
      y = df.mean(axis=0, numeric_only=True)
      sem = df.sem(axis=0, numeric_only=True)
      y_upper = y + sem
      y_lower = y - sem
      line_color = "hsla(" + line_hue + ",100%,50%,1)"
      error_band_color = "hsla(" + line_hue + ",100%,50%,0.125)"
      figure.extend([
      go.Scatter(
        name=str(ind_var),
        x=x,
        y=y,
        line=dict(color=line_color),
        mode='lines',
      ),
      go.Scatter(
        name=str(ind_var)+"-upper",
          x=x,
          y=y_upper,
          mode='lines',
          marker=dict(color=error_band_color),
          line=dict(width=0),
          # showlegend=False,
      ),
      go.Scatter(
          name=str(ind_var)+"-lower",
          x=x,
          y=y_lower,
          marker=dict(color=error_band_color),
          line=dict(width=0),
          mode='lines',
          fillcolor=error_band_color,
          fill='tonexty',
          # showlegend=False,
      )
    ])
    plotly_fig = go.Figure(figure)
    plotly_fig.update_layout(
      yaxis_title=yaxis_title,
      xaxis_title="Episodes",
      title=plot_title,
    )
    return plotly_fig

  
  def get_cpr_plot(self, results, desc):
    plot_title = "Community CPR of " + desc if self.is_community else "Mean Agent CPR of " + desc
    return self.get_plot(results, plot_title, "Cumulative Pseudo Regret")
  
  def get_poa_plot(self, results, desc):
    plot_title = "POA of " + desc
    return self.get_plot(results, plot_title, "Probability of Optimal Action")

  def display_and_save(self, results, desc):
    cpr_plot = self.get_cpr_plot(results[0], desc)
    poa_plot = self.get_poa_plot(results[1], desc)
    elapsed_time = time.time() - self.start_time
    print_info = "Time Elapsed: {}d {}h {}m {}s".format(\
      int(elapsed_time // (60 * 60 * 24)),\
      int(elapsed_time // (60 * 60)),\
      int(elapsed_time // 60 % 60),\
      int(elapsed_time % 60)
    )
    print(f'{print_info}{" " * (60 - len(print_info))}')

    if self.show:
      cpr_plot.show()
      poa_plot.show()
    if self.save:
      file_name = "{}_N{}".format(desc, self.get_N())
      dir_path = "../output/%s" % file_name
      mkdir(dir_path)
      cpr_plot.write_html(dir_path + "/cpr.html")
      poa_plot.write_html(dir_path + "/poa.html")
      self.saved_data.to_csv(dir_path + "/last_episode_data.csv")
      with open(dir_path + '/values.json', 'w') as outfile:
        dump(self.values, outfile)
      
  def run(self, desc=""):
    if desc:
      print(desc)
    print("seed=%d | N=%d" % (self.seed, self.get_N()))
    results = self.combine_results(self.multithreaded_sim())
    self.display_and_save(results, desc)
    
  def get_N(self):
    if self.is_community:
      return self.num_threads * self.MC_sims
    return self.num_threads * self.MC_sims * self.num_agents
  
  def get_values(self, locals):
    values = {key: val for key, val in locals.items() if key != 'self'}
    parsed_env_dicts = []
    for env in values["environment_dicts"]:
      parsed_env = {}
      for node, model in env.items():
        parsed_env[node] = str(model)
      parsed_env_dicts.append(parsed_env)
    values["environment_dicts"] = tuple(parsed_env_dicts)
    values["seed"] = self.seed
    return values

if __name__ == "__main__":
  baseline = {
    "Z": RandomModel((0.5, 0.5)),
    "X": ActionModel(("Z"), (0, 1)),
    "W": DiscreteModel(("X"), {(0,): (0.9, 0.1), (1,): (0.1, 0.9)}),
    "Y": DiscreteModel(("Z", "W"), {(0, 0): (0.9, 0.1), (0, 1): (0.7, 0.3), (1, 0): (0.7, 0.3), (1, 1): (0.05, 0.95)})
  }
  reversed_w = dict(baseline)
  reversed_w["W"] = DiscreteModel(("X"), {(0,): (0.1, 0.9), (1,): (0.9, 0.1)})

  experiment = Sim(
    environment_dicts=(baseline, reversed_w, baseline, reversed_w),
    policy="Sensitive",#("Solo", "Sensitive", "Adjust"),
    asr="TS",#("EG", "EF", "ED","TS"),
    T=500,
    MC_sims=250,
    div_node_conf=(0.1, 0.2, 0.3, 0.4, 0.5),
    EG_epsilon=0.07,
    EF_rand_trials=28,
    ED_cooling_rate=0.9,
    is_community=False,
    rand_envs=True,
    env_mutation_chance=0.5,
    show=True,
    save=True,
    seed=None
  )
  experiment.run(desc="DNC Comparison with Sensitive Agent using Thompson Sampling")
