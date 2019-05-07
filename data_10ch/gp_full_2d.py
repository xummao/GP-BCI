"""
We fit GPs to the full dataset, testing different models and kernels
"""

from load_matlab import *
from gp_full_1d import *
import numpy as np
import GPy
import matplotlib.pyplot as plt
import pickle
import os, os.path as path
import h5py
import itertools
import argparse
import copy

parser = argparse.ArgumentParser()
parser.add_argument('--uid', type=str, default=1, help='uid for job number')
parser.add_argument('--dt', type=int, default=0, choices=(0,10,20,40,60,80,100), help='dt. one of (0,10,20,40,60,80,100)')
parser.add_argument('--emg', type=int, default=2, choices=range(7), help='emg. between 0-6')

# DEFAULT
dt=0
emg=2

def make_dataset_2d(trainsC, emg=emg, syn=None, dt=dt, means=False, n=None):
    if syn is not None:
        assert type(syn) is tuple, "syn should be a tuple of 2 emgs. (eg. (0,4))"
    trains = trainsC.get_emgdct(emg)
    X = []
    Y = []
    Xmean = []
    Ymean = []
    for i,ch1 in enumerate(CHS):
        for j,ch2 in enumerate(CHS):
            xych1 = ch2xy[ch1]
            xych2 = ch2xy[ch2]
            # Note that here we are only taking the pairs (ch1,ch2)
            # But will miss (ch2,ch1). These are the same data (for
            # dt=0),but the GP should still have this info
            # However, with 2000 pts its already slow enough so we
            # don't include them for now (this is prob bad though)
            # train time: 4000 pts = 10 mins
            #             2000 pts = 2 mins
            if syn:
                emg1,emg2 = syn
                ys = trainsC.synergy(emg1,emg2,ch1,ch2,dt).max(axis=1)
            else:
                ys = trains[ch1][ch2][dt]['data'].max(axis=1)
            if n:
                ys = random.sample(trains[ch1][ch2][dt]['data'].max(axis=1).tolist(),n)
            Y.extend(ys)
            X.extend([xych1 + xych2]*len(ys))
            # we also make a small dataset with means
            Ymean.append(ys.mean())
            Xmean.extend([xych1 + xych2])
    if means:
        Xmean = np.array(Xmean)
        Ymean = np.array(Ymean).reshape((-1,1))
        return Xmean, Ymean
    X = np.array(X)
    Y = np.array(Y).reshape((-1,1))
    return X,Y

class Abs(GPy.core.Mapping):
    def __init__(self, mapping):
        input_dim, output_dim = mapping.input_dim, mapping.output_dim
        assert(output_dim == 1)
        super(Abs, self).__init__(input_dim=input_dim, output_dim=output_dim)
        self.mapping = mapping
        self.link_parameters(mapping)

    def f(self, X):
        return np.abs(self.mapping.f(X))

    def update_gradients(self, dL_dF, X):
        None

    def gradients_X(self, dL_dF, X):
        if X >= 0:
            # TODO: should this be 1/-1?
            return dL_dF
        else:
            return -dL_dF


def build_prior(m1d, dtprior=False):
    f1 = GPy.core.Mapping(4,1)
    def f_1(x):
        return m1d.predict(x[:,0:2])[0]
    f1.f = f_1
    f1.update_gradients = lambda a,b: None
    
    f2 = GPy.core.Mapping(4,1)
    def f_2(x):
        return m1d.predict(x[:,2:4])[0]
    f2.f = f_2
    f2.update_gradients = lambda a,b: None

    if not dtprior:
        # prior = a*f1 + b*f2
        mf = GPy.mappings.Additive(GPy.mappings.Compound(f1, GPy.mappings.Linear(1,1)),
                                   GPy.mappings.Compound(f2, GPy.mappings.Linear(1,1)))
        mf.mapping.linmap.A = mf.mapping_1.linmap.A = 1/2
    else:
        # prior = c*(a*f1+b*f2) + d*|a*f1-b*f2| (where c and d could
        # be exponentials related to dt in the future)
        negf = GPy.mappings.Linear(1,1)
        negf.A = -1
        negf.fix()
        a = GPy.mappings.Linear(1,1)
        b = GPy.mappings.Linear(1,1)
        # a*f1 + b*f2
        mfadd = GPy.mappings.Additive(GPy.mappings.Compound(f1, a),
                                      GPy.mappings.Compound(f2, b))
        # |a*f1 - b*f2|
        # NOTE: this doesn't work at the moment
        # When added to mfsub, 'a' will somehow remove the 'a' from mfadd
        mfsub = Abs(GPy.mappings.Additive(GPy.mappings.Compound(f1, a),
                                          GPy.mappings.Compound(GPy.mappings.Compound(f2, b), negf)))
        mf = GPy.mappings.Additive(GPy.mappings.Compound(mfadd, GPy.mappings.Linear(1,1)),
                                   GPy.mappings.Compound(mfsub, GPy.mappings.Linear(1,1)))
    return mf

def train_models_2d(X,Y, kerneltype='add', symkern=False, num_restarts=1, prior1d=None, optimize=True, ARD=False, dtprior=False):
    # Additive kernel
    if kerneltype == 'add':
        k1 = GPy.kern.Matern52(input_dim=2, active_dims=[0,1], ARD=ARD)
        k2 = GPy.kern.Matern52(input_dim=2, active_dims=[2,3], ARD=ARD)
        k = k1 + k2
        if prior1d:
            k.Mat52.lengthscale = k.Mat52_1.lengthscale = prior1d.Mat52.lengthscale
            k.Mat52.variance = k.Mat52_1.variance = prior1d.Mat52.variance
    # Full SE
    elif kerneltype == 'mult':
       k = GPy.kern.Matern52(input_dim=4, ARD=ARD)
       if prior1d:
           k.lengthscale = prior1d.Mat52.lengthscale
           k.variance = prior1d.Mat52.variance
    else:
        raise Exception("kerneltype should be add or mult")
    # Symmetric SE
    if symkern:
        symM = np.block([[np.zeros((2,2)),np.eye(2)],[np.eye(2),np.zeros((2,2))]])
        k = GPy.kern.Symmetric(k, symM)

    if prior1d:
        m = GPy.models.GPRegression(X,Y,k, mean_function= build_prior(prior1d, dtprior=dtprior))
        m.Gaussian_noise.variance = prior1d.Gaussian_noise.variance
    else:
        m = GPy.models.GPRegression(X,Y,k)
    if optimize:
        m.optimize_restarts(num_restarts=num_restarts)

    return m

def make_add_model(X,Y,prior1d=None, prevmodel=None, ARD=False, dtprior=False):
    k1 = GPy.kern.Matern52(input_dim=2, active_dims=[0,1], ARD=ARD)
    k2 = GPy.kern.Matern52(input_dim=2, active_dims=[2,3], ARD=ARD)
    k = k1 + k2
    if dtprior:
        # This is just a hack because dtprior prior is not
        # implemented properly. Somehow we can't .copy() it since its
        # parameters are hidden somewhere... in this case we can't use
        # previous model's hyperparameters and need to reoptimize for
        # kernel/mapping parameters each seq optimization (make sure continue_opt=True)
        if prevmodel:
            m = GPy.models.GPRegression(X,Y,kernel=prevmodel.sum.copy(), mean_function= build_prior(prior1d, dtprior=dtprior))
        else:
            m = GPy.models.GPRegression(X,Y,k, mean_function= build_prior(prior1d, dtprior=dtprior))
        return m
    if prevmodel and prior1d:
        m = GPy.models.GPRegression(X,Y,kernel=prevmodel.sum.copy(), mean_function=prevmodel.mapping.copy())
        m[:] = prevmodel[:]
    elif prevmodel:
        #There is a previous model but it doesn't use prior (so no
        #mean mapping)
        m = GPy.models.GPRegression(X,Y,kernel=prevmodel.sum.copy())
        m.Gaussian_noise.variance = prevmodel.Gaussian_noise.variance
    elif prior1d:
        #We are building a model for first time, but with a 1d prior
        m = GPy.models.GPRegression(X,Y,k, mean_function=build_prior(prior1d, dtprior=dtprior))
        m.sum.Mat52.lengthscale = m.sum.Mat52_1.lengthscale = prior1d.Mat52.lengthscale
        m.sum.Mat52.variance = m.sum.Mat52_1.variance = prior1d.Mat52.variance
        m.Gaussian_noise.variance = prior1d.Gaussian_noise.variance
    else:
        m = GPy.models.GPRegression(X,Y,k)
    return m

def train_model_seq_2d(trainsC, n_random_pts=10, n_total_pts=15, num_restarts=1, ARD=False, prior1d=None, fix=False, continue_opt=True, emg=emg, syn=None, dt=dt, dtprior=False, sa=True, symkern=False, multkern=False, T=0.001):
    trains = trainsC.get_emgdct(emg)
    if dtprior:
        assert(continue_opt), "if dtprior is True, must set continue_opt to true"
        assert(prior1d is not None), "if dtprior is True, must give prior1d"
    X = []
    Y = []
    for _ in range(n_random_pts):
        ch1 = random.choice(CHS)
        ch2 = random.choice(CHS)
        X.append(ch2xy[ch1] + ch2xy[ch2])
        if syn is None:
            resp = random.choice(trains[ch1][ch2][dt]['data'].max(axis=1))
        else:
            resp = random.choice(trainsC.synergy(syn[0], syn[1], ch1, ch2, dt).max(axis=1))
        Y.append(resp)
    #We save every model after each query
    models = []

    if symkern:
        m = train_models_2d(np.array(X),np.array(Y)[:,None], prior1d=prior1d, ARD=ARD, kerneltype='mult',symkern=True)
    elif multkern:
        m = train_models_2d(np.array(X),np.array(Y)[:,None], prior1d=prior1d, ARD=ARD, kerneltype='mult')
    else:
        m = make_add_model(np.array(X),np.array(Y)[:,None], prior1d=prior1d, ARD=ARD, dtprior=dtprior)
        if fix:
            # fix all kernel parameters and only optimize for mean (prior) mapping
            m.sum.fix()
            m.Gaussian_noise.fix()
        m.optimize_restarts(num_restarts=num_restarts)
    models.append(m)
    for _ in range(n_total_pts-n_random_pts):
        nextx = get_next_x(m, sa=sa, T=T)
        X.append(nextx)
        ch1 = xy2ch[nextx[0]][nextx[1]]
        ch2 = xy2ch[nextx[2]][nextx[3]]
        if syn is None:
            resp = random.choice(trains[ch1][ch2][dt]['data'].max(axis=1))
        else:
            resp = random.choice(trainsC.synergy(syn[0], syn[1], ch1, ch2, dt).max(axis=1))
        Y.append(resp)
        if symkern:
            m = train_models_2d(np.array(X),np.array(Y)[:,None], prior1d=prior1d, ARD=ARD, kerneltype='mult',symkern=True)
        elif multkern:
            m = train_models_2d(np.array(X),np.array(Y)[:,None], prior1d=prior1d, ARD=ARD, kerneltype='mult') 
        else:
            m = make_add_model(np.array(X), np.array(Y)[:,None], prior1d=prior1d, prevmodel=models[-1], ARD=ARD, dtprior=dtprior)
            # If continue optimize, we optimize params after every query
            if continue_opt:
                m.optimize_restarts(num_restarts=num_restarts)
        models.append(m)
        dct = {
            'models': models,
            'nrnd': n_random_pts,
            'ntotal': n_total_pts
        }
    return dct

def softmax(x, T=0.001):
    e = np.exp(x/T)
    return e/sum(e)

def get_next_x(m, k=2, sa=False, T=0.001):
    X,acq = get_acq_map(m,k)
    if sa:
        # SA is for simulated annealing (sample instead of taking max)
        # For now we just normalize (since all >0). We could try
        # softmax instead
        normalize = acq.flatten()/sum(acq)
        sm = softmax(acq, T=T).flatten()
        nextidx = np.random.choice(range(len(acq)), p=sm)
    else:
        nextidx = acq.argmax()
    nextx = X[nextidx]
    return nextx

def get_acq_map(m, k=2):
    # We use UCB, k is the "exploration" parameter
    X = np.array(list(itertools.product(range(2),range(5), range(2), range(5))))
    mean,var = m.predict(X)
    std = np.sqrt(var)
    acq = mean + k*std
    return X,acq

def make_2d_grid():
    return np.array(list(itertools.product(range(2),range(5), range(2), range(5))))

# Code for generating plots
def plot_model_2d(m, fulldatam=None, title="", plot_acq=False, plot_reverse=False):
    if type(m) is dict:
        nrnd = m['nrnd']; is_seq=True
        m = m['models'][-1]
    else: is_seq=False
    if plot_reverse:
        fig, axes = plt.subplots(9,5, sharex=True, sharey=True)
    else:
        fig, axes = plt.subplots(4,5, sharex=True, sharey=True)
    fig.suptitle(title)
    for i in [0,1]:
        for j in range(5):
            ch1 = xy2ch[i][j]
            title='ch1 = {}'.format(ch1)
            axes[2*i][j].set_title(title)
            if plot_reverse:
                title='ch2 = {}'.format(ch1)
                axes[2*i+5][j].set_title(title)
            for x2i in [0,1]:
                ax = axes[2*i+x2i][j]
                m.plot(ax=ax, fixed_inputs=[(0,i),(1,j),(2,x2i)], plot_data=False, legend=False)
                # We also plot the max found
                maxx = m.predict(make_2d_grid())[0].max()
                x = np.arange(0,4,0.1)
                ax.plot(x,np.ones(len(x))*maxx, c='r')
                # And the mean of the full-data-gp, if present
                if fulldatam:
                    fulldatam.plot_mean(ax=ax, fixed_inputs=[(0,i),(1,j),(2,x2i)], color='y')
                if plot_reverse:
                    ax2 = axes[2*i+x2i+5][j]
                    m.plot(ax=ax2, fixed_inputs=[(0,i),(1,j),(2,x2i)], plot_data=False, legend=False)
                    ax2.plot(x,np.ones(len(x))*maxx, c='r')
    # We also plot the data
    if is_seq:
        for (x1i,x1j,x2i,x2j),y in zip(m.X[:nrnd], m.Y[:nrnd]):
            x1i,x1j,x2i,x2j = int(x1i), int(x1j), int(x2i), int(x2j)
            ax = axes[2*x1i+x2i][x1j]
            ax.plot(x2j, y, 'x', color='k')
        t=1
        norm = colors.Normalize(vmin=0, vmax=len(m.X)-nrnd)
        for (x1i,x1j,x2i,x2j),y in zip(m.X[nrnd:], m.Y[nrnd:]):
            x1i,x1j,x2i,x2j = int(x1i), int(x1j), int(x2i), int(x2j)
            ax = axes[2*x1i+x2i][x1j]
            ax.plot(x2j, y, 'x', color=plt.cm.Reds(norm(t)))
            t+=1
    else:
        for (x1i,x1j,x2i,x2j),y in zip(m.X, m.Y):
            x1i,x1j,x2i,x2j = int(x1i), int(x1j), int(x2i), int(x2j)
            ax = axes[2*x1i+x2i][x1j]
            ax.plot(x2j, y, 'x', color='C{}'.format(x2j))
            if plot_reverse:
                x1i,x1j,x2i,x2j = int(x1i), int(x1j), int(x2i), int(x2j)
                ax = axes[2*x2i+x1i+5][x2j]
                ax.plot(x1j, y, 'x', color='C{}'.format(x1j))
    if plot_acq:
        plt.figure()
        _,acq = get_acq_map(m)
        sm = softmax(acq).reshape((10,10))
        plt.imshow(sm)
        plt.colorbar()

def plot_model_2d_reverse(m, fulldatam=None, title="", plot_acq=False):
    # same as plot_model_2d, except here we plot with ch2 in foreground
    fig, axes = plt.subplots(4,5, sharex=True, sharey=True)
    fig.suptitle(title)
    for i in [0,1]:
        for j in range(5):
            ch2 = xy2ch[i][j]
            title='ch2 = {}'.format(ch2)
            axes[2*i][j].set_title(title)
            for x2i in [0,1]:
                ax = axes[2*i+x2i][j]
                m.plot(ax=ax, fixed_inputs=[(2,i),(3,j),(0,x2i)], plot_data=False, legend=False)
                
                # We also plot the max found
                maxx = m.predict(make_2d_grid())[0].max()
                x = np.arange(0,4,0.1)
                ax.plot(x,np.ones(len(x))*maxx, c='r')
                # And the mean of the full-data-gp, if present
                if fulldatam:
                    fulldatam.plot_mean(ax=ax, fixed_inputs=[(2,i),(3,j),(0,x2i)], color='y')
    for (x1i,x1j,x2i,x2j),y in zip(m.X, m.Y):
        x1i,x1j,x2i,x2j = int(x1i), int(x1j), int(x2i), int(x2j)
        ax = axes[2*x2i+x1i][x2j]
        ax.plot(x1j, y, 'x', color='C{}'.format(x1j))
    if plot_acq:
        plt.figure()
        _,acq = get_acq_map(m)
        sm = softmax(acq).reshape((10,10))
        plt.imshow(sm)
        plt.colorbar()

def l2dist(m1, m2):
    X = make_2d_grid()
    pred1 = m1.predict(X)[0]
    pred2 = m2.predict(X)[0]
    return LA.norm(pred1-pred2)
def linfdist(m1, m2):
    X = np.array(list(itertools.product(range(2),range(5), range(2), range(5))))
    pred1 = m1.predict(X)[0]
    pred2 = m2.predict(X)[0]
    # Note that in the 1d linfdist, we divide by pred2.max() so as to normalize
    return abs(pred1.max() - pred2.max())

def get_ch_pair(wxyz):
    w,x,y,z = wxyz
    return [get_ch([w,x]), get_ch([y,z])]

def get_maxchpair(m):
    X = np.array(list(itertools.product(range(2),range(5), range(2), range(5))))
    means,_ = m.predict(X)
    maxidx = means.argmax()
    maxwxyz = np.unravel_index(maxidx, (2,5,2,5))
    maxchpair = get_ch_pair(maxwxyz)
    return maxchpair

def run_ch_stats_exps(trainsC, emg=emg, dt=dt, uid='', repeat=25, continue_opt=True, k=2, dtprior=False, ntotal=100, nrnd = [15,76,10], sa=True, multkern=False, symkern=False, ARD=False):
    if uid == '':
        uid = random.randrange(10000)
    assert(type(nrnd) is list and len(nrnd) == 3)
    trains = trainsC.get_emgdct(emg)
    nrnd = range(*nrnd)
    exppath = path.join('exps', '2d', 'exp{}'.format(uid), 'emg{}'.format(emg), 'dt{}'.format(dt), 'sa{}'.format(sa), 'multkern{}'.format(multkern), 'symkern{}'.format(symkern), 'ARD{}'.format(ARD))
    if not path.isdir(exppath):
        os.makedirs(exppath)
    n_ch = 2 # pair of channel for 2d experiment
    n_models = 3 if dtprior else 2
    # Build 1d model for modelsprior
    X1d,Y1d = make_dataset_1d(trainsC, emg=emg)
    X = np.array(list(itertools.product(range(2),range(5), range(2), range(5))))
    m1d, = train_models_1d(X1d,Y1d, ARD=False)
    # queriedchs contains <n_ch> queried channels for all <repeat> runs of <ntotal>
    # queries with <nrnd> initial random pts for each of <n_models> models
    queriedchs = np.zeros((n_models, repeat, len(nrnd), ntotal, n_ch))
    maxchs = np.zeros((n_models, repeat, len(nrnd), ntotal, n_ch))
    vals = np.zeros((n_models, repeat, len(nrnd), ntotal, 100))
    for repeat in range(repeat):
        print("Repeat", repeat)
        for i,n1 in enumerate(nrnd):
            print(n1, "random init pts")
            modelsD = train_model_seq_2d(trainsC,n_random_pts=n1, n_total_pts=ntotal,
                                         num_restarts=1, continue_opt=continue_opt, ARD=ARD,
                                        dt=dt, emg=emg, sa=sa, multkern=multkern, symkern=symkern)
            modelspriorD = train_model_seq_2d(trainsC,n_random_pts=n1, n_total_pts=ntotal,
                                             num_restarts=1, continue_opt=continue_opt,
                                             prior1d=m1d, dt=dt, emg=emg, dtprior=False,
                                              sa=sa, multkern=multkern, symkern=symkern, ARD=ARD)
            models = modelsD['models']
            modelsprior = modelspriorD['models']
            queriedchs[0][repeat][i] = [get_ch_pair(xy) for xy in models[-1].X]
            queriedchs[1][repeat][i] = [get_ch_pair(xy) for xy in modelsprior[-1].X]
            for r,m in enumerate(models,n1-1):
                maxchs[0][repeat][i][r] = get_maxchpair(m)
                vals[0][repeat][i][r] = m.predict(X)[0].reshape((-1))
            for r,m in enumerate(modelsprior,n1-1):
                maxchs[1][repeat][i][r] = get_maxchpair(m)
                vals[1][repeat][i][r] = m.predict(X)[0].reshape((-1))
            if dtprior:
                modelsdtpriorD = train_model_seq_2d(trainsC,n_random_pts=n1, n_total_pts=ntotal,
                                             num_restarts=1, continue_opt=continue_opt,
                                                   prior1d=m1d, dt=dt, emg=emg, dtprior=True,
                                                    sa=sa, multkern=multkern, symkern=symkern, ARD=ARD)
                modelsdtprior = modelsdtpriorD['models']
                queriedchs[2][repeat][i] = [get_ch_pair(xy) for xy in modelsdtprior[-1].X]
                for r,m in enumerate(modelsdtprior,n1-1):
                    maxchs[2][repeat][i][r] = get_maxchpair(m)
                    vals[2][repeat][i][r] = m.predict(X)[0].reshape((-1))
    dct = {
        'queriedchs': queriedchs,
        'maxchs': maxchs,
        'vals': vals,
        'nrnd': nrnd,
        'true_vals': trainsC.build_f_grid(emg=emg, dt=dt).flatten(),
        'ntotal': ntotal,
        'emg': emg,
        'dt': dt,
        'uid': uid,
        'repeat': repeat,
        'true_chpair': trainsC.max_ch_2d(emg,dt),
        'multkern': multkern,
        'symkern': symkern
    }
    filename = os.path.join(exppath, 'chruns2d_dct.pkl')
    print("Saving stats dictionary to: {}".format(filename))
    with open(filename, 'wb') as f:
        pickle.dump(dct, f)
    return dct

def run_dist_exps(args):
    emgdtpath = path.join('exps', 'emg{}'.format(args.emg), 'dt{}'.format(args.dt))
    exppath = path.join(emgdtpath, 'exp{}'.format(args.uid))
    if not path.isdir(exppath):
        os.makedirs(exppath)

    trainsC = Trains(emg=args.emg)
    trains = trainsC.get_emgdct(args.emg)

    X1d,Y1d = make_dataset_1d(trains)
    m1d, = train_models_1d(X1d,Y1d, ARD=False)

    X,Y = make_dataset_2d(trainsC, emg=args.emg, dt=args.dt)
    
    # Note that the full-data models can be shared for all exps (with
    # same emg and dt).
    # Hence we save them in emgdtpath instead of exppath
    addpriorpath = path.join(emgdtpath, 'maddprior.h5')
    if os.path.exists(addpriorpath):
        with h5py.File(addpriorpath) as f:
            maddprior, = train_models_2d(X,Y, prior1d=m1d, optimize=False)
            maddprior[:] = f['param_array']
    else:
        maddprior, = train_models_2d(X,Y, prior1d=m1d)
        maddprior.save(addpriorpath)

    addpath = path.join(emgdtpath, 'madd.h5')
    if path.exists(addpath):
        with h5py.File(addpath) as f:
            madd, = train_models_2d(X,Y, optimize=False)
            madd[:] = f['param_array']
    else:
        madd, = train_models_2d(X,Y)
        madd.save(addpath)
    
    # We train all models with n rnd start pts and m sequential pts
    # And compare them to the model trained with all datapts
    # Then compute statistics and plot them
    nrnd = range(10,100,10)
    nseq = range(0,100,10)
    N = 50
    l2s = np.zeros((3, N, len(nrnd),len(nseq)))
    linfs = np.zeros((3, N, len(nrnd),len(nseq)))
    for k in range(N):
        print("Starting loop", k)
        for i,n1 in enumerate(nrnd):
            for j,n2 in enumerate(nseq):
                print(n1,n2)
                modelsD = train_model_seq_2d(trains,n_random_pts=n1, n_total_pts=n1+n2, dt=args.dt)
                modelspriorD = train_model_seq_2d(trains,n_random_pts=n1, n_total_pts=n1+n2, prior1d=m1d, dt=args.dt)
                modelspriorfixD = train_model_seq_2d(trains,n_random_pts=n1, n_total_pts=n1+n2, prior1d=m1d, fix=True, dt=args.dt)
                models = modelsD['models']
                modelsprior = modelspriorD['models']
                modelspriorfix = modelspriorD['models']
                mnoprior, mprior, mpriorfix = models[-1], modelsprior[-1], modelspriorfix[-1]
                for midx,m in enumerate([mnoprior, mprior, mpriorfix]):
                    l2 = l2dist(m, madd)
                    linf = linfdist(m, madd)
                    l2s[midx][k][i][j] = l2
                    linfs[midx][k][i][j] = linf
        np.save(os.path.join(exppath,"l2s"), l2s)
        np.save(os.path.join(exppath, "linfs"), linfs)

        for i,name in enumerate(["","_prior","_priorfix"]):
            plt.figure()
            plt.imshow(l2s[i][:k+1].mean(axis=0), extent=[0,100,100,10])
            plt.title("2d l2 dist to full model{}".format(name))
            plt.ylabel("N random pts")
            plt.xlabel("N sequential")
            plt.colorbar()
            plt.savefig(os.path.join(exppath, "2d_l2{}_{}.png".format(name,k)))
            plt.close()

            plt.figure()
            plt.imshow(linfs[i][:k+1].mean(axis=0), extent=[0,100,100,10])
            plt.title("2d linf dist to full model{}".format(name))
            plt.ylabel("N random pts")
            plt.xlabel("N sequential")
            plt.colorbar()
            plt.savefig(os.path.join(exppath, "2d_linf{}_{}.png".format(name,k)))
            plt.close()



if __name__ == "__main__":
    args = parser.parse_args()
    dt = args.dt
    emg = args.emg
    trainsC = Trains(emg=args.emg)
    trains = trainsC.get_emgdct(args.emg)

    #TODO: make m1d (prior1d) work with synergy in
    #      run_ch_stats_exps AND train_model_seq_2d

    D = run_ch_stats_exps(trainsC, emg=4, dt=0, uid=9306, repeat=2, ntotal=100,
                          nrnd=[30,51,10], sa=True, multkern=True, symkern=True, ARD=True)

    emg=4
    X1d,Y1d = make_dataset_1d(trainsC, emg=4)
    m1d, = train_models_1d(X1d,Y1d, ARD=False)
    m1dard = train_models_1d(X1d,Y1d, ARD=True)

    # model_names = 'all'
    # X,Y = make_dataset_2d(trainsC, emg=4, dt=10, means=True)
    # models = train_models_2d(X,Y, models=model_names, ARD=False)
    # modelsard = train_models_2d(X,Y, models=model_names, ARD=True)
    # modelsprior = train_models_2d(X,Y, prior1d=m1d, models=model_names, ARD=False)
    # modelspriorard = train_models_2d(X,Y, prior1d=m1d, models=model_names, ARD=True)
    # for ms in [models,modelsard,modelsprior,modelspriorard]:
    #     for m in ms:
    #         plot_model_2d(m)
            
    X,Y = make_dataset_2d(trainsC, emg=4, dt=10, means=True)
    msymmult = train_models_2d(X,Y, kerneltype='mult', symkern=True, ARD=True, prior1d=m1d)

    mdct = train_model_seq_2d(trainsC, 50, 100, emg=4, dt=10, prior1d=m1d, symkern=True, sa=False, ARD=True, multkern=True)
    mdctsa = train_model_seq_2d(trainsC, 50, 100, emg=4, dt=10, prior1d=m1d, symkern=True, sa=True, ARD=True, multkern=True)

    plot_model_2d(mdct, plot_acq=True)
    plot_model_2d(mdctsa, plot_acq=True)
    # for m in models[-10:]:
    #     plot_model_2d(m)
    # m = models[-1]
    
    plt.show()
