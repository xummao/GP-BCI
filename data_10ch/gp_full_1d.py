"""
We fit GPs to the full dataset, testing different models and kernels
"""

# Idea:
# Query new points twice + fit heteroskedastic noise to empirical data
import matplotlib as mpl
mpl.rcParams['pdf.fonttype'] = 42
from load_matlab import *
import numpy as np
import GPy
import matplotlib.pyplot as plt
import matplotlib.colors as colors
from numpy import linalg as LA
import argparse
import os
from os import path
import itertools
import pickle

parser = argparse.ArgumentParser()
parser.add_argument('--uid', type=int, default=2, help='uid for job number')
parser.add_argument('--emg', type=int, default=2, choices=range(7), help='emg. between 0-6')

# DEFAULT
# there is no dt in 1d. But we need this to access the trains dct
dt=0

def make_dataset_1d(trains, mean=False, n=20):
    # n means number of datapt per channel
    # Used to test/debug certain models
    X = []
    Y = []
    Xmean = []
    Ymean = []
    Yvars = []
    for ch in CHS:
        ys = random.sample(trains[ch][ch][dt]['data'].max(axis=1).tolist(),n)
        Y.extend(ys)
        var = trains[ch][ch][dt]['stdmax'] ** 2
        Yvars.extend([var] * len(ys))
        xy = ch2xy[ch]
        X.extend([xy]*len(ys))
        Ymean.append(trains[ch][ch][dt]['meanmax'])
        Xmean.extend([xy])
    if mean:
        Xmean = np.array(Xmean)
        Ymean = np.array(Ymean).reshape((-1,1))
        return Xmean, Ymean
    X = np.array(X)
    Y = np.array(Y).reshape((-1,1))
    return X,Y

# Heteroskedastic model
# Yvars = np.array(Yvars).reshape((-1,1))
# matk = GPy.kern.Matern52(input_dim=2, ARD=True)
# mhetefix = GPy.models.GPHeteroscedasticRegression(X,Y,matk)
# # We use the real noise
# mhetefix.het_Gauss.variance = Yvars
# mhetefix.het_Gauss.fix()
# mhetefix.optimize(messages=True,max_f_eval = 1000)

def train_models_1d(X,Y, model_names=['homo'], num_restarts=5, ARD=True):
    if model_names == 'all':
        model_names = ['homo', 'hete']
    models = []
    if 'hete' in model_names:
        matk = GPy.kern.Matern52(input_dim=2, ARD=ARD)
        mhete = GPy.models.GPHeteroscedasticRegression(X,Y,matk)
        mhete.optimize(messages=True,max_f_eval = 1000)
        models.append(mhete)

    if 'homo' in model_names:
        matk = GPy.kern.Matern52(input_dim=2, ARD=ARD)
        mhomo = GPy.models.GPRegression(X,Y,matk)
        mhomo.optimize_restarts(num_restarts=num_restarts)
        models.append(mhomo)

    return models

def train_model_seq(trains, n_random_pts=10, n_total_pts=15, ARD=True, num_restarts=3, continue_opt=False, k=2):
    X = []
    Y = []
    for _ in range(n_random_pts):
        ch = random.choice(CHS)
        X.append(ch2xy[ch])
        resp = random.choice(trains[ch][ch][dt]['data'].max(axis=1))
        Y.append(resp)
    matk = GPy.kern.Matern52(input_dim=2, ARD=ARD)
    #Make model
    models = []
    m = GPy.models.GPRegression(np.array(X),np.array(Y)[:,None],matk)
    m.optimize_restarts(num_restarts=num_restarts, messages=False)
    # We optimize this kernel once and then use it for all future models
    matk = m.kern
    gnoise = m.Gaussian_noise.variance
    models.append(m)
    for _ in range(n_total_pts-n_random_pts):
        nextx = get_next_x(m, k=k)
        X.append(nextx)
        ch = xy2ch[nextx[0]][nextx[1]]
        resp = random.choice(trains[ch][ch][dt]['data'].max(axis=1))
        Y.append(resp)
        m = GPy.models.GPRegression(np.array(X), np.array(Y)[:,None],matk.copy())
        m.Gaussian_noise.variance = gnoise.copy()
        ## TODO: also set gp's noise variance to be same as previous!
        if continue_opt:
            m.optimize_restarts(num_restarts=num_restarts, messages=False)
        models.append(m)
    return models

def plot_seq_values(m, n_random_pts, trainsC=None, ax=None, legend=False):
    if ax is None:
        ax = plt.figure()
    ax.plot(m.Y[:n_random_pts+1], c='b', label="{} random init pts".format(n_random_pts))
    ax.plot(range(n_random_pts, len(m.Y)), m.Y[n_random_pts:,:], c='r', label="Sequential pts")
    ax.set_title("Value of selected channel")
    if trainsC:
        maxch = trainsC.max_ch()
        for i,resp in enumerate(m.Y):
            x,y = m.X[i]
            ch = xy2ch[int(x)][int(y)]
            if ch == maxch:
                ax.plot(i, resp, 'x', c='k')
    if legend:
        ax.legend()

def plot_conseq_dists(m, n_random_pts, trainsC = None, ax=None, legend=False):
    if ax is None:
        ax = plt.figure()
    dists = [LA.norm(m.X[i]-m.X[i+1]) for i in range(len(m.X)-1)]
    ax.plot(dists[:n_random_pts+1], c='b', label="{} random init pts".format(n_random_pts))
    ax.plot(range(n_random_pts, len(dists)), dists[n_random_pts:], c='r', label="Sequential pts")
    ax.set_title("Distance between consecutive channels")
    if trainsC:
        maxch = trainsC.max_ch()
        label="max channel ({})".format(maxch)
        for i,dist in enumerate(dists):
            x,y = m.X[i]
            ch = xy2ch[int(x)][int(y)]
            if ch == maxch:
                ax.plot(i, dist, 'x', c='k', label=label)
                label=None
    if legend:
        ax.legend()

def plot_seq_stats(m, n_random_pts, trainsC=None, title=None, plot_acq=False, plot_gp=True):
    fig = plt.figure()
    ax1 = fig.add_subplot(2,2,2)
    ax2 = fig.add_subplot(2,2,4)
    # We first plot sequential values
    plot_seq_values(m, n_random_pts, trainsC=trainsC, ax=ax1)
    # Then distance between consecutive x's
    plot_conseq_dists(m, n_random_pts, trainsC=trainsC, ax=ax2, legend=True)
    # Finally we plot the GP with the data points
    ax3 = fig.add_subplot(2,2,1)
    ax4 = fig.add_subplot(2,2,3,sharex=ax3, sharey=ax3)
    axes = [ax3,ax4]
    lengthscales = [m.Mat52.lengthscale[i] for i in range(len(m.Mat52.lengthscale))]
    fig.suptitle(("{}: ls="+" {:.2} "*len(lengthscales)).format(title,*lengthscales))
    for i,ax in zip([0,1],axes):
        m.plot(ax=ax, fixed_inputs=[(0,i)],
               plot_data=False, title='Channels {}'.format(xy2ch[i]),
               lower=17, upper=83, legend=False)
    # Plot data (m.plot plots all of the data in every slice, which is
    # wrong)
    axes[int(m.X[0][0])].plot(int(m.X[0][1]), m.Y[0][0], '+', label="{} random init pts".format(n_random_pts))
    for (i,j),y in zip(m.X[1:n_random_pts,:], m.Y[1:n_random_pts,:]):
        i,j = int(i), int(j)
        axes[i].plot(j, y, '+', c='b')
    t=1
    norm = colors.Normalize(vmin=0, vmax=len(m.X)-n_random_pts)
    for (i,j),y in zip(m.X[n_random_pts:,:], m.Y[n_random_pts:,:]):
        i,j = int(i), int(j)
        axes[i].plot(j, y, 'x', color=plt.cm.Reds(norm(t)))
        t+=1
    if plot_acq:
        acqmap = get_acq_map(m)
        axes[1].plot(acqmap[:5], c='y', label='acq fct')
        axes[0].plot(acqmap[5:], c='y', label='acq fct')
    axes[0].legend()

def get_acq_map(m, k=2):
    # We use UCB, k is the "exploration" parameter
    mean,var = m.predict(np.array(list(ch2xy.values())))
    std = np.sqrt(var)
    acq = mean + k*std
    return acq

def get_next_x(m, k=2):
    acq = get_acq_map(m,k)
    maxidx = acq.argmax()
    maxch = CHS[maxidx]
    xy = ch2xy[maxch]
    return xy
    
def plot_model_1d(m, title=None, plot_acq=False, plot_data=True):
    print(m)
    print(m.kern)
    print(m.kern.lengthscale)

    fig, axes = plt.subplots(2,1,
                             sharex=True,
                             sharey=True)
    lengthscales = [m.Mat52.lengthscale[i] for i in range(len(m.Mat52.lengthscale))]
    #fig.suptitle(("{}: ls="+" {:.2}
    #"*len(lengthscales)).format(title,*lengthscales))
    if title:
        title = 'Channels {}'.format(xy2ch[i])
    for i,ax in zip([0,1],axes):
        m.plot(ax=ax, fixed_inputs=[(0,i)],
               plot_data=False, title=title,
               lower=17, upper=83, legend=False)
        #m.plot_confidence(ax=ax, fixed_inputs=[(0,i)], label='std', lower=17, upper=83)
        #m.plot_mean(ax=ax, fixed_inputs=[(0,i)])
        # Plot data (m.plot plots all of the data in every slice, which is
        # wrong)
    if plot_data:
        t=1
        norm = colors.Normalize(vmin=-50, vmax=len(m.X))
        for (i,j),y in zip(m.X, m.Y):
            i,j = int(i), int(j)
            axes[i].plot(j, y, 'x', color='C{}'.format(j))
            t+=1
    if plot_acq:
        acqmap = get_acq_map(m)
        axes[1].plot(acqmap[:5], c='y', label='acq fct')
        axes[0].plot(acqmap[5:], c='y', label='acq fct')
    axes[0].legend()
    axes[0].set_xticks([0,1,2,3,4])
        # for j,ch in enumerate(xy2ch[i]):
        #     ax.plot(np.ones(20)*j,trains[ch][ch][dt]['data'].max(axis=1),'x')
        #     ax.plot(j, trains[ch][ch][dt]['meanmax'], 'r+')
        #     ax.errorbar(j, trains[ch][ch][dt]['meanmax'], yerr=2*trains[ch][ch][dt]['stdmax'],ecolor='r')

def plot_model_surface(m, plot_data=True):
    extra_lim=1
    x = np.linspace(0-extra_lim,1+extra_lim,50)
    y = np.linspace(0-extra_lim,4+extra_lim,50)
    x,y = np.meshgrid(x,y)
    x_,y_ = x.ravel()[:,None], y.ravel()[:,None]
    z = np.hstack((x_,y_))
    mean,var = m.predict(z)
    std = np.sqrt(var)
    mean,std = mean.reshape(50,50), std.reshape(50,50)

    fig2 = plt.figure()
    ax = fig2.gca(projection='3d')
    ax.set_title("Color represents std")
    norm = plt.Normalize()
    surf = ax.plot_surface(x, y, mean, linewidth=0, antialiased=False, facecolors=cm.jet(norm(std)))
    if plot_data:
        ax.scatter(m.X[:,0], m.X[:,1], m.Y, c='g')
    m = cm.ScalarMappable(cmap=plt.cm.jet, norm=norm)
    m.set_array([])
    plt.colorbar(m)


def l2dist(m1, m2):
    X = np.array(list(itertools.product(range(2), range(5))))
    pred1 = m1.predict(X)[0]
    pred2 = m2.predict(X)[0]
    return LA.norm(pred1-pred2)
def linfdist(m1, m2):
    X = np.array(list(itertools.product(range(2), range(5))))
    pred1 = m1.predict(X)[0]
    pred2 = m2.predict(X)[0]
    return abs((pred1.max() - pred2.max())/pred2.max())

def run_dist_exps(args):
    exppath = path.join('exps', '1d', 'emg{}'.format(args.emg), 'exp{}'.format(args.uid))
    if not path.isdir(exppath):
        os.makedirs(exppath)
    trainsC = Trains(emg=args.emg)
    trains = trainsC.trains

    # We train all models with n rnd start pts and m sequential pts
    # And compare them to the model trained with all datapts
    # Then compute statistics and plot them
    X,Y = make_dataset_1d(trains)
    mfull, = train_models_1d(X,Y, ARD=False)
    nrnd = range(5,50,5)
    nseq = range(0,50,5)
    N = 50
    l2s = np.zeros((N, len(nrnd),len(nseq)))
    linfs = np.zeros((N, len(nrnd),len(nseq)))
    for k in range(N):
        print("Starting loop", k)
        for i,n1 in enumerate(nrnd):
            for j,n2 in enumerate(nseq):
                print(n1,n2)
                models = train_model_seq(trains,n_random_pts=n1, n_total_pts=n1+n2, ARD=False)
                m = models[-1]
                l2 = l2dist(m, mfull)
                linf = linfdist(m, mfull)
                l2s[k][i][j] = l2
                linfs[k][i][j] = linf
        np.save(os.path.join(exppath,"l2s"), l2s)
        np.save(os.path.join(exppath, "linfs"), linfs)

    plt.imshow(l2s.mean(axis=0), extent=[0,50,50,5])
    plt.title("1d l2 dist to true gp mean")
    plt.ylabel("N random pts")
    plt.xlabel("N sequential")
    plt.colorbar()
    plt.savefig(os.path.join(exppath, "1d_l2.pdf"))
    plt.close()

    plt.figure()
    plt.imshow(linfs.mean(axis=0), extent=[0,50,50,5])
    plt.title("1d linf dist to true gp mean")
    plt.ylabel("N random pts")
    plt.xlabel("N sequential")
    plt.colorbar()
    plt.savefig(os.path.join(exppath, "1d_linf.pdf"))
    plt.close()

def get_ch(xy):
    x,y = xy
    x = int(x)
    y = int(y)
    return xy2ch[x][y]

def get_maxch(m):
    X = np.array(list(itertools.product(range(2),range(5))))
    means,_ = m.predict(X)
    maxidx = means.argmax()
    maxxy = np.unravel_index(maxidx, (2,5))
    maxch = get_ch(maxxy)
    return maxch

def run_ch_stats_exps(trains, args, repeat=25, continue_opt=True, k=2):
    exppath = path.join('exps', '1d', 'chruns', 'emg{}'.format(args.emg), 'exp{}'.format(args.uid))
    if not path.isdir(exppath):
        os.makedirs(exppath)
    ntotal=100
    nrnd = range(5,76,10)
    queriedchs = np.zeros((repeat, len(nrnd), ntotal))
    maxchs = np.zeros((repeat, len(nrnd), ntotal))
    for repeat in range(repeat):
        print("Repeat", repeat)
        for i,n1 in enumerate(nrnd):
            print(n1)
            models = train_model_seq(trains, n_random_pts=n1,
                                     n_total_pts=ntotal, ARD=False,
                                     continue_opt=continue_opt, num_restarts=1, k=k)
            queriedchs[repeat][i] = [get_ch(xy) for xy in models[-1].X]
            for r,m in enumerate(models,n1-1):
                maxchs[repeat][i][r] = get_maxch(m)
    dct = {
        'queriedchs': queriedchs,
        'maxchs': maxchs,
        'nrnd': nrnd,
        'ntotal': ntotal,
        'true_ch': 17
    }
    with open(os.path.join(exppath, 'chruns_dct.pkl'), 'wb') as f:
        pickle.dump(dct, f)
    return queriedchs, maxchs

def run_seq_runs_exps(trainsC):
    fig,ax = plt.subplots(1,1)
    n_total=60
    nrnd = range(10,n_total-10,10)
    for i,n1 in enumerate(nrnd):
        models = train_model_seq(trains, n_random_pts=n1, n_total_pts=n_total, ARD=False)
        m = models[-1]
        ax.plot(range(n1, len(m.Y)), m.Y[n1:,:], label="Sequential pts", c='C{}'.format(i))
        if trainsC:
            maxch = trainsC.max_ch()
            x,y = m.X[-1]
            ch = xy2ch[int(x)][int(y)]
            if ch == maxch:
                ax.plot(len(m.X)-1, m.Y[-1], 'x', c='C{}'.format(i))
    plt.title("Runs with different number of random seed pts")

if __name__ == '__main__':
    # args = parser.parse_args()
    # trainsC = Trains(emg=args.emg)
    # trains = trainsC.trains
    X,Y = make_dataset_1d(trains)
    m1, = train_models_1d(X,Y)
    m2, = train_models_1d(X,Y, ARD=False)
    # plot_model_1d(m1, title="homoskedastic")
    # plot_model_1d(m2, title="homoskedastic")
    # plt.show()

    # test seq model
    n_rnd = 45
    n_total=90
    models = train_model_seq(trains, n_random_pts=n_rnd, n_total_pts=n_total, ARD=False, k=2)
    # plot_seq_stats(models[-1], n_random_pts=n_rnd, trainsC=trainsC, plot_acq=True, plot_gp=False)
    
    plt.show()

