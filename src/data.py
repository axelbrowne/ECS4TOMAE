from math import inf
from util import permutations, kl_divergence

def pairs(lst):
  return [(a, b) for i, a in enumerate(lst) for b in lst[i + 1:]]

class DataSet(list):
  def __init__(self, data=[]):
    super().__init__(data)

  def is_empty(self):
    return len(self) == 0
    
  def query(self, query_dict):
    res = DataSet()
    for e in self:
      consistent = True
      for key in query_dict:
        if e[key] != query_dict[key]:
          consistent = False
          break
      if consistent:
        res.append(e)
    return res
    
  def mean(self, var):
    total = len(self)
    return sum([e[var] for e in self]) / total if total else None
  
  def optimal_choice(self, rng, act_dom, rew_var, givens):
    best_choice = []
    best_rew = -inf
    for choice in permutations(act_dom):
      expected_rew = self.query({**choice, **givens}).mean(rew_var)
      if expected_rew is not None:
        if expected_rew > best_rew:
          best_choice = [choice]
          best_rew = expected_rew
        elif expected_rew == best_rew:
          best_choice.append(choice)
    return rng.choice(best_choice) if best_choice else None


class DataBank:
  def __init__(self, domains, act_var, rew_var, data={}, divergence={}):
    self.data = data
    self.domains = domains
    self.vars = set(domains.keys())
    self.act_var = act_var
    self.rew_var = rew_var
    self.divergence = divergence
    for key in self.data:
      self.add_agent(key)
  
  def add_agent(self, new_agent):
    if new_agent in self:
      return
    self.data[new_agent] = DataSet()
    self.divergence[new_agent] = {}
    for a in self.data.keys():
      self.divergence[new_agent][a] = {}
      self.divergence[a][new_agent] = {}
      for node in self.get_non_act_nodes():
        div_val = 1 if a != new_agent else 0
        self.divergence[a][new_agent][node] = div_val
        self.divergence[new_agent][a][node] = div_val
    
        
  def get_non_act_nodes(self):
    return [node for node in self.vars if node != self.act_var]
        
  def kl_div_of_query(self, query, P_agent, Q_agent):
    return kl_divergence(self.domains, self.data[P_agent], self.data[hash(Q_agent)], query)
  
  def kl_div_of_node(self, node, P_agent, Q_agent):
    return self.kl_div_of_query(P_agent.environment.cgm.get_node_dist(node), P_agent, Q_agent)
        
  def update_divergence(self):
    for P_agent, P_data in self.data.items():
      # if len(P_data) < P_agent.samps_needed:
      #     break
      for Q_agent, Q_data in self.data.items():
        if P_agent == Q_agent:
          continue
        for node in self.get_non_act_nodes():
          query = P_agent.environment.cgm.get_node_dist(node)
          self.divergence[P_agent][Q_agent][node] = kl_divergence(self.domains, P_data, Q_data, query)
    return
  
  def div_nodes(self, P_agent, Q_agent):
    if P_agent == Q_agent:
      return []
    return [node for node, divergence in self.divergence[P_agent][Q_agent].items() if divergence is None or abs(divergence) > P_agent.div_node_conf]

  def all_data(self):
    data = DataSet()
    [data.extend(d) for d in self.data.values()]
    return data
  
  def sensitive_data(self, P_agent):
    data = DataSet()
    [data.extend(Q_data) for Q_agent, Q_data in self.data.items() if len(self.div_nodes(P_agent, Q_agent)) == 0]
    return data

  def items(self):
    return self.data.items()
  
  def __iter__(self):
    return self.data.__iter__()

  def __getitem__(self, key):
    return self.data[key]
  
  def __reduce__(self):
    return type(self), (self.domains, self.act_var, self.rew_var, self.data, self.divergence)