from openvqe.circuit import QCircuit
from openvqe.circuit.compiler import compile_controlled_rotation
from openvqe.circuit._gates_impl import ParametrizedGateImpl, RotationGateImpl, PowerGateImpl
from openvqe.objective import Objective
from openvqe import OpenVQEException
from openvqe import copy
from openvqe import numpy as np
from openvqe.circuit.variable import Variable,Transform,has_variable,Add,Sub,Inverse,Pow,Mul,Div

def weight_chain(par,var):
    '''
    Because Transform objects are at most
    '''
    if type(par) is Variable:
        if par == var:
            return 1.0
        else:
            return 0.0

    elif type(par) is Transform:
        t=par
        la=len(t.args)
        expan=np.empty(la)

        if t.has_var(var):
            for i in range(la):
                if has_variable(t.args[i],var):
                    expan[i]=tgrad(t.f,argnum=i)(*t.args)*weight_chain(t.args[i],var)
                else:
                    expan[i]=0.0


        return np.sum(expan)
        
    else:
        s='Object of type {} passed to weight_chain; only Variables and Transforms are allowed.'.format(str(type(par)))
        raise OpenVQEException(s)
                
def tgrad(f,argnum):
    '''
    function to be replaced entirely by the use of jax.grad(); completely identical thereto but restricted to our toy usecases.
    '''
    assert callable(f)

    if argnum == 0:

        if f is Add:
            return lambda x,y: 1.0

        elif f is Inverse:
            return  lambda x: -1/(x**2)

        elif f is Mul:
            return lambda x,y: y

        elif f is Sub:
            return lambda x,y: 1.0

        elif f is Div:
            return lambda x,y: 1/y

        elif f is Pow:
            return lambda x,y: y*x**(y-1)

        else:
            raise OpenVQEException('Sorry, only pre-built openvqe functions supported for tgrad at the moment.')


    elif argnum ==1:

        if f is Add:
            return lambda x,y: 1.0

        elif f is Inverse:
            return  lambda x: -1/(x**2)

        elif f is Mul:
            return lambda x,y: x

        elif f is Sub:
            return lambda x,y: -1.0

        elif f is Div:
            return lambda x,y: -x/(y**2)

        elif f is Pow:
            return lambda x,y: (x**(y))*np.log(x)


    else:
        raise OpenVQEException('sorry, only functions with up to two arguments are supported at present')




def grad(obj):
    if isinstance(obj, QCircuit):
        return grad_unitary(unitary=obj)
    elif isinstance(obj, Objective):
        return grad_objective(objective=obj)
    elif isinstance(obj, ParametrizedGateImpl):
        return grad_unitary(QCircuit.wrap_gate(gate=obj))
    else:
        raise OpenVQEException("Gradient not implemented for other types than QCircuit or Objective")


def grad_unitary(unitary: QCircuit):
    gradient = []
    for var in unitary.parameters:
        gradient.append(make_gradient_component(unitary=unitary,var=var))
    return gradient


def grad_objective(objective: Objective):
    if len(objective.unitaries) > 1:
        raise OpenVQEException("Gradient of Objectives with more than one unitary not supported yet")
    result= grad_unitary(objective.unitaries[0])
    for i in result:
        i.observable=objective.observable
    return result


def make_gradient_component(unitary: QCircuit, var):
    """
    :param unitary: the unitary
    :return: dU/dpi as list of Objectives
    """
    dg = []
    pi=np.pi
    for i,g in enumerate(unitary.gates):
        found=False
        if g.is_parametrized() and not g.is_frozen():
            if type(g.parameter) is Variable:
                if g.parameter==var:
                    found=True
            elif type(g.parameter) is Transform:
                for p in g.parameter.variables:
                    if p.name == var.name and p._value ==var._value:
                        found=True

        if found==True:
                found = False     
                if isinstance(g, RotationGateImpl):
                    if g.is_controlled():
                        angles_and_weights = [
                            ([(g.angle / 2) + pi / 2, -g.angle / 2],.50),
                            ([(g.angle ) / 2 - pi / 2, -g.angle / 2],-.50),
                            ([g.angle / 2, -(g.angle / 2)  + pi / 2],-.50),
                            ([g.angle / 2, -(g.angle / 2) - pi / 2],.50)
                        ]
                        for ang_set in angles_and_weights:

                            U = unitary.replace_gate(position=i,gates=[gate for gate in compile_controlled_rotation(g, angles=ang_set[0])])
                            U.weight=0.5*ang_set[1]*weight_chain(g.parameter,var)
                            dg.append(U)
                    else:
                        neo_a = copy.deepcopy(g)
                        neo_a.frozen=True

                        neo_a.angle = g.angle + pi/2
                        U1 = unitary.replace_gate(position=i,gates=[neo_a])
                        U1.weight = 0.5*weight_chain(g.parameter,var)

                        neo_b = copy.deepcopy(g)
                        neo_b.frozen=True
                        neo_b.angle = g.angle - pi/2
                        U2=unitary.replace_gate(position=i,gates=[neo_b])
                        U2.weight = -0.5*weight_chain(g.parameter,var)
                        dg.append(U1)
                        dg.append(U2)
                elif isinstance(g, PowerGateImpl):
                    
                    if g.is_controlled():
                        raise NotImplementedError("Gradient for controlled PowerGate not here yet")
                    else:
                        n_pow = g.parameter*pi/4
                        target=g.target
                        ### does that need to be divided by two?
                        ### trying to convert gates to rotations for quadrature
                        if g.name in ['H','Hadamard']:

                            U1 = unitary.replace_gate(position=i,gates=[RotationGateImpl(axis=1,target=target,angle=(n_pow+pi/2),frozen=True)])
                            U2 = unitary.replace_gate(position=i,gates=[RotationGateImpl(axis=1,target=target,angle=(n_pow-pi/2),frozen=True)])
                            U1.weight=0.5*weight_chain(g.parameter,var)
                            U2.weight=-0.5*weight_chain(g.parameter,var)
                            dg.extend([U1,U2])
 
                        else:
                            n_pow = g.parameter*pi
                            if g.name in ['X','x']:
                                axis=0
                            elif g.name in ['Y','y']:
                                axis=1
                            elif g.name in ['Z','z']:
                                axis=2
                            else:
                                raise NotImplementedError('sorry, I have no idea what this gate is and cannot build the gradient.')
                            U1 = unitary.replace_gate(position=i,gates=[RotationGateImpl(axis=axis,target=target,angle=(n_pow+pi/2),frozen=True)])
                            U2 = unitary.replace_gate(position=i,gates=[RotationGateImpl(axis=axis,target=target,angle=(n_pow-pi/2),frozen=True)])

                            U1.weight=0.5*weight_chain(g.parameter,var)
                            U2.weight=-0.5*weight_chain(g.parameter,var)
                            dg.extend([U1,U2])
                    
                else:
                    raise OpenVQEException("Automatic differentiation is implemented only for Rotational Gates")

    return Objective(unitaries=dg)