# Besides some added methods, most of this class was written by Iain Barr (ijmbarr on GitHub)
# from his public repository, causalgraphicalmodels
# The code has been imported and modified into this project for ease/consistency
import inspect
import networkx as nx

from cgm import CausalGraph
from assignment_models import AssignmentModel

class StructuralCausalModel:
  def __init__(self, assignment):
    """
    Creates StructuralCausalModel from assignment of the form
    { variable: Function(parents) }
    """
    self.assignment = assignment.copy()
    nodes = list(assignment.keys())
    set_nodes = []
    edges = []
    for node, model in assignment.items():
      if model is None:
        set_nodes.append(node)
      elif isinstance(model, AssignmentModel):
        edges.extend([
          (parent, node)
          for parent in model.parents
          ])
      elif callable(model):
        sig = inspect.signature(model)
        parents = [
          parent
          for parent in sig.parameters.keys()
          if parent != "n_samples"
        ]
        self.assignment[node] = AssignmentModel(parents, model)
        edges.extend([(p, node) for p in parents])
      else:
        raise ValueError("Model must be either callable or None. "
            "Instead got {} for node {}."
            .format(model, node))

      self.cgm = CausalGraph(
        nodes=nodes, edges=edges, set_nodes=set_nodes
      )

  def __repr__(self):
    variables = ", ".join(map(str, sorted(self.cgm.dag.nodes())))
    return ("{classname}({vars})"
        .format(classname=self.__class__.__name__,vars=variables))

  def sample(self, set_values={}):
    """
    Sample from CSM

    Arguments
    ---------
    n_samples: int
        the number of samples to return

    set_values: dict[variable:str, set_value:np.array]
        the values of the interventional variable

    Returns
    -------
    samples: pd.DataFrame
    """
    samples = {}

    for node in nx.topological_sort(self.cgm.dag):
      c_model = self.assignment[node]

      if c_model.model is None:
          samples[node] = set_values[node]
      else:
        parent_samples = {
          parent: samples[parent]
          for parent in c_model.parents
          }
        samples[node] = c_model(**parent_samples)

    return samples

  def do(self, node):
    """
    Returns a StructualCausalModel after an intervention on node
    """
    new_assignment = self.assignment.copy()
    new_assignment[node] = None
    return CausalGraph(new_assignment)
