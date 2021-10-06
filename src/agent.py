from query import Product, Query
from util import permutations, only_dicts_with_givens
from data import DataSet
from enums import ASR


class Agent:
  def __init__(self, rng, name, environment, databank, tau=None, asr=ASR.EG, epsilon=0, rand_trials=0, cooling_rate=0):
    self.rng = rng
    self.name = name
    self.environment = environment
    self.databank = databank
    self.tau = tau
    self.asr = asr
    self.feat_perms = permutations(environment.get_feat_doms())
    self.epsilon = [1] * len(self.feat_perms) if asr == ASR.ED else epsilon
    self.rand_trials = rand_trials
    self.rand_trials_rem = [rand_trials] * \
        len(self.feat_perms) if self.feat_perms else rand_trials
    self.cooling_rate = cooling_rate
    self.act_var = environment.get_act_var()
    self.act_dom = environment.get_act_dom()
    self.rew_var = environment.get_rew_var()
    self.rew_dom = environment.get_rew_dom()

    self.databank.add_agent(self)

  def get_recent(self):
    return self.databank[self][-1]

  def get_ind_var_value(self, ind_var):
    if ind_var == "tau":
      return self.tau
    elif ind_var == "otp":
      return self.get_otp()
    elif ind_var == "asr":
      return self.asr
    elif ind_var == "epsilon":
      return self.epsilon
    elif ind_var == "rand_trials":
      return self.rand_trials
    elif ind_var == "cooling_rate":
      return self.cooling_rate
    else:
      return ""

  def communicate(self, agents):
    pass

  def act(self):
    givens = self.environment.pre.sample(self.rng)
    choice = self.choose(givens)
    givens |= choice
    observation = self.environment.post.sample(self.rng, givens)
    self.databank[self].append(observation)

  def choose(self, givens):
    if self.asr == ASR.EG:
      if self.rng.random() < self.epsilon:
        return self.choose_random()
      return self.choose_optimal(givens)
    elif self.asr == ASR.EF:
      if self.feat_perms:
        given_i = self.feat_perms.index(givens)
        if self.rand_trials_rem[given_i] > 0:
          self.rand_trials_rem[given_i] -= 1
          return self.choose_random()
      elif self.rand_trials_rem > 0:
        self.rand_trials_rem -= 1
        return self.choose_random()
      return self.choose_optimal(givens)
    elif self.asr == ASR.ED:
      if self.feat_perms:
        given_i = self.feat_perms.index(givens)
        if self.rng.random() < self.epsilon[given_i]:
          self.epsilon[given_i] *= self.cooling_rate
          return self.choose_random()
      elif self.rng.random() < self.epsilon:
        self.epsilon *= self.cooling_rate
        return self.choose_random()
      self.epsilon *= self.cooling_rate
    elif self.asr == ASR.TS:
      return self.thompson_sample(givens)
    else:
      raise ValueError("%s ASR not found" % self.asr)

  def choose_optimal(self, givens):
    pass

  def choose_random(self):
    return self.rng.choice(permutations(self.act_dom))

  def thompson_sample(self, givens):
    pass

  def ts_from_dataset(self, dataset, givens):
    choice = None
    max_sample = 0  # float('-inf')
    data = dataset.query(givens)
    for action in permutations(self.act_dom):
      alpha = len(data.query({**action, **{self.rew_var: 1}}))
      beta = len(data.query({**action, **{self.rew_var: 0}}))
      sample = self.rng.beta(alpha + 1, beta + 1)
      if sample > max_sample:
        choice = action
        max_sample = sample
    return choice

  def get_otp(self):
    return self.__class__.__name__[:-5]

  def __hash__(self):
    return hash(self.name)

  def __reduce__(self):
    return (self.__class__, (self.rng, self.name, self.environment, self.databank, self.tau, self.asr, self.epsilon, self.rand_trials, self.cooling_rate))

  def __repr__(self):
    return "<" + self.get_otp() + self.name + ": " + self.asr.value + ">"

  def __eq__(self, other):
    return isinstance(other, self.__class__) \
        and self.name == other.name


class SoloAgent(Agent):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

  def communicate(self, agents):
    return

  def choose_optimal(self, givens):
    optimal = self.databank[self].optimal_choice(
        self.rng, self.act_dom, self.rew_var, givens)
    return optimal if optimal else self.choose_random()

  def thompson_sample(self, givens):
    return self.ts_from_dataset(self.databank[self], givens)


class NaiveAgent(Agent):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

  def communicate(self, agents):
    for a in agents:
      if a == self:
        continue
      self.knowledge.listen(a.knowledge.recent)

  def choose_optimal(self, givens):
    optimal = self.databank[self].optimal_choice(
        self.rng, self.act_dom, self.rew_var, givens)
    return optimal if optimal else self.choose_random()

  def thompson_sample(self, givens):
    return self.ts_from_dataset(self.databank.all_data(), givens)


class SensitiveAgent(Agent):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)

  def communicate(self, agents):
    for a in agents:
      if a == self:
        continue
      elif self.div_nodes(a):
        return

  def choose_optimal(self, givens):
    optimal = self.databank.sensitive_data(self).optimal_choice(
        self.rng, self.act_dom, self.rew_var, givens)
    return optimal if optimal else self.choose_random()

  def thompson_sample(self, givens):
    return self.ts_from_dataset(self.databank.sensitive_data(self), givens)


class AdjustAgent(SensitiveAgent):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.act_var = self.environment.get_act_var()

  def has_S_node(self, node, other):
    return node in self.div_nodes(other)

  def div_nodes(self, other):
    return self.databank.div_nodes(self, other)

  def get_CPTs(self):
    div_nodes = {a: self.div_nodes(a) for a in self.databank}
    CPTs = {}
    for node in self.environment.get_non_act_vars():
      CPTs[node] = DataSet()
      for agent, data in self.databank.items():
        if node not in div_nodes[agent]:
          CPTs[node].extend(data)
    return CPTs

  def choose_optimal(self, givens):
    CPTs = self.get_CPTs()
    max_val = 0
    choices = []
    for action in permutations(self.act_dom):
      expected_value = self.get_expected_value(CPTs, action, givens)
      if expected_value > max_val:
        max_val = expected_value
        choices = [action]
      elif expected_value == max_val:
        choices.append(action)
    return self.rng.choice(choices)

  def solve_query(self, query):
    node = query.var()
    transportable_data = DataSet()
    for agent in self.databank:
      if node not in self.div_nodes(agent):
        transportable_data.extend(self.databank[agent])
    return query.solve(transportable_data)

  def all_causal_path_nodes_corrupted(self, agent):
    return self.environment.cgm.causal_path(self.act_var, self.rew_var).issubset(set(self.div_nodes(agent)))

  def thompson_sample(self, givens):
    """
    For new 'large chain' model
    """
    max_sample = 0
    choices = []
    for action in permutations(self.act_dom):
      alpha = 0
      beta = 0
      for agent in self.databank:
        if self.all_causal_path_nodes_corrupted(agent):
          continue
        summA, summB = 0, 0
        for s in (0, 1):
          for r in (0, 1):
            alpha_y_prob = self.solve_query(Query({"Y": 1}, {"R": r}))
            # 1 - alpha_y_prob if alpha_y_prob is not None else None
            beta_y_prob = self.solve_query(Query({"Y": 0}, {"R": r}))
            r_prob = self.solve_query(Query({"R": r}, {"S": s}))
            s_prob = self.solve_query(Query({"S": s}, action))
            if alpha_y_prob is None or r_prob is None or s_prob is None:
              continue
            summA += alpha_y_prob * r_prob * s_prob
            summB += beta_y_prob * r_prob * s_prob
        if (0.99 > (summA + summB)) or ((summA + summB) > 1.01):
          print('\n', alpha_y_prob, beta_y_prob)
        count = self.databank[agent].num({**action, **givens})
        alpha += summA * count
        beta += summB * count
      sample = self.rng.beta(alpha + 1, beta + 1)
      if sample > max_sample:
        max_sample = sample
        choices = [action]
      if sample == max_sample:
        choices.append(action)
    return self.rng.choice(choices)

  # def get_expected_value(self, CPTs, action, givens):
  #   prob = 0
  #   for w in (0,1):
  #     y_prob = Query({"Y": 1}, {**{"W": w}, **givens}).solve(CPTs["Y"])
  #     w_prob = Query({"W": w}, action).solve(CPTs["W"])
  #     if y_prob is None or w_prob is None:
  #       prob += 0
  #       continue
  #     prob += y_prob * w_prob
  #   return prob

  def get_expected_value(self, CPTs, action, givens):
    """
    For new 'large chain' model
    """
    prob = 0
    for s in (0, 1):
      for r in (0, 1):
        y_prob = Query({"Y": 1}, {"R": r}).solve(CPTs["Y"])
        r_prob = Query({"R": r}, {"S": s}).solve(CPTs["R"])
        s_prob = Query({"S": s}, action).solve(CPTs["S"])
        if y_prob is None or r_prob is None or s_prob is None:
          prob += 0
          continue
        prob += y_prob * r_prob * s_prob
    return prob
