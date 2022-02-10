"""
@author: Maziar Raissi
"""

import sys
sys.path.insert(0, 'C:/Users/ketil/Desktop/UiB/Jobb/Publication/PINNs (Maziarraissi TF1)/PINNs/Utilities/')
#sys.path.insert(0, '../../Utilities/')

import tensorflow as tf
import numpy as np
import matplotlib.pyplot as plt
import scipy.io
from scipy.interpolate import griddata
from plotting import newfig, savefig
from mpl_toolkits.axes_grid1 import make_axes_locatable
import matplotlib.gridspec as gridspec
import time

import xarray as xr

np.random.seed(1234)
tf.set_random_seed(1234)

class PhysicsInformedNN:
    # Initialize the class
    def __init__(self, X_f, X_u, u, layers, lb, ub):
        
        self.lb = lb
        self.ub = ub
        
        # Residual points
        self.x_f = X_f[:,0:1]
        self.t_f = X_f[:,1:2]
        
        # Supervised points
        self.x_u = X_u[:,0:1]
        self.t_u = X_u[:,1:2]
        self.u = u
        
        # NN layers
        self.layers = layers
        
        # Initialize NNs
        self.weights, self.biases = self.initialize_NN(layers)
        
        # For custom NN
        self.encoder_weights_1 = self.xavier_init([2, layers[1]])
        self.encoder_biases_1 = self.xavier_init([1, layers[1]])
        
        self.encoder_weights_2 = self.xavier_init([2, layers[1]])
        self.encoder_biases_2 = self.xavier_init([1, layers[1]])
        
        # tf placeholders and graph
        self.sess = tf.Session(config=tf.ConfigProto(allow_soft_placement=True,
                                                     log_device_placement=True))
        
        # Initialize parameters
        self.lambda_1 = tf.Variable([0.0], dtype=tf.float32)
        self.lambda_2 = tf.Variable([-6.0], dtype=tf.float32)
        
        # Residual points
        self.x_f_tf = tf.placeholder(tf.float32, shape=[None, self.x_f.shape[1]])
        self.t_f_tf = tf.placeholder(tf.float32, shape=[None, self.t_f.shape[1]])
        
        # Supervised points
        self.x_u_tf = tf.placeholder(tf.float32, shape=[None, self.x_u.shape[1]])
        self.t_u_tf = tf.placeholder(tf.float32, shape=[None, self.t_u.shape[1]])
        self.u_tf = tf.placeholder(tf.float32, shape=[None, self.u.shape[1]])
         
        
        self.u_pred = self.net_u(self.x_u_tf, self.t_u_tf)
        self.f_pred = self.net_f(self.x_f_tf, self.t_f_tf)
        
        
        # Adaptive constant
        self.beta = 0.9
        self.adaptive_constant_val = np.array(1.0)
        self.adaptive_constant_tf = tf.placeholder(tf.float32, shape=self.adaptive_constant_val.shape)
        
        
        # Loss
        self.loss_res = tf.reduce_mean(tf.square(self.f_pred))
        self.loss_bcs = self.adaptive_constant_tf * tf.reduce_mean(tf.square(self.u_tf - self.u_pred))
        
        self.loss = self.loss_res + self.loss_bcs
        
        
        # Adaptive constant cont.
        self.grad_res = []
        self.grad_bcs = []
        for i in range(len(self.layers) - 1):
            self.grad_res.append(tf.gradients(self.loss_res, self.weights[i])[0])
            self.grad_bcs.append(tf.gradients(self.loss_bcs, self.weights[i])[0])
            
        self.adaptive_constant_log = []
        self.adaptive_constant_list = []
        
        self.max_grad_res_list = []
        self.mean_grad_bcs_list = []
        
        for i in range(len(self.layers) - 1):
            self.max_grad_res_list.append(tf.reduce_max(tf.abs(self.grad_res[i])))
            self.mean_grad_bcs_list.append(tf.reduce_mean(tf.abs(self.grad_bcs[i])))
            
        self.max_grad_res = tf.reduce_max(tf.stack(self.max_grad_res_list))
        self.mean_grad_bcs = tf.reduce_mean(tf.stack(self.mean_grad_bcs_list))
        self.adaptive_constant = self.max_grad_res / self.mean_grad_bcs
        
        
        # Lbfgs Optimizer
        self.Lbfgs_iter = 0
        self.optimizer = tf.contrib.opt.ScipyOptimizerInterface(self.loss, 
                                                                method = 'L-BFGS-B', 
                                                                options = {'maxiter': 50000,
                                                                           'maxfun': 50000,
                                                                           'maxcor': 50,
                                                                           'maxls': 50,
                                                                           'ftol' : 1.0 * np.finfo(float).eps})
        # ADAM Optimizer
        self.optimizer_Adam = tf.train.AdamOptimizer()
        self.train_op_Adam = self.optimizer_Adam.minimize(self.loss)
        
        init = tf.global_variables_initializer()
        self.sess.run(init)

    def initialize_NN(self, layers):        
        weights = []
        biases = []
        num_layers = len(layers) 
        for l in range(0,num_layers-1):
            W = self.xavier_init(size=[layers[l], layers[l+1]])
            b = tf.Variable(tf.zeros([1,layers[l+1]], dtype=tf.float32), dtype=tf.float32)
            weights.append(W)
            biases.append(b)        
        return weights, biases
        
    def xavier_init(self, size):
        in_dim = size[0]
        out_dim = size[1]        
        xavier_stddev = np.sqrt(2/(in_dim + out_dim))
        return tf.Variable(tf.truncated_normal([in_dim, out_dim], stddev=xavier_stddev), dtype=tf.float32)
    
    def neural_net(self, X, weights, biases):
        num_layers = len(weights) + 1
        
        H = 2.0*(X - self.lb)/(self.ub - self.lb) - 1.0
        for l in range(0,num_layers-2):
            W = weights[l]
            b = biases[l]
            H = tf.tanh(tf.add(tf.matmul(H, W), b))
        W = weights[-1]
        b = biases[-1]
        Y = tf.add(tf.matmul(H, W), b)
        return Y
    
    def forward_pass(self, H):
        num_layers = len(self.layers)
        encoder_1 = tf.tanh(tf.add(tf.matmul(H, self.encoder_weights_1), self.encoder_biases_1))
        encoder_2 = tf.tanh(tf.add(tf.matmul(H, self.encoder_weights_2), self.encoder_biases_2))

        for l in range(0, num_layers - 2):
            W = self.weights[l]
            b = self.biases[l]
            H = tf.math.multiply(tf.tanh(tf.add(tf.matmul(H, W), b)), encoder_1) + \
                tf.math.multiply(1 - tf.tanh(tf.add(tf.matmul(H, W), b)), encoder_2)

        W = self.weights[-1]
        b = self.biases[-1]
        H = tf.add(tf.matmul(H, W), b)
        return H
            
    def net_u(self, x, t):  
        u = self.neural_net(tf.concat([x,t],1), self.weights, self.biases) # Normal NN
        #u = self.forward_pass(tf.concat([x,t],1))                           # Custom NN
        
        return u
    
    def net_f(self, x, t):
        lambda_1 = self.lambda_1        
        lambda_2 = tf.exp(self.lambda_2)
        u = self.net_u(x,t)
        u_t = tf.gradients(u, t)[0]
        u_x = tf.gradients(u, x)[0]
        u_xx = tf.gradients(u_x, x)[0]
        f = u_t + lambda_1*u*u_x - lambda_2*u_xx
        
        return f
    
    def callback(self, loss, lambda_1, lambda_2):
        if self.Lbfgs_iter % 100 == 0:
            print('LBFGS It: %d, Loss: %e, lambda_1: %.5f, lambda_2: %.5f' % (self.Lbfgs_iter, loss, lambda_1, np.exp(lambda_2)))
        self.Lbfgs_iter += 1
        
    def train(self, nIter):
        tf_dict = {self.x_f_tf: self.x_f, self.t_f_tf: self.t_f,
                   self.x_u_tf: self.x_u, self.t_u_tf: self.t_u, self.u_tf: self.u,
                   self.adaptive_constant_tf: self.adaptive_constant_val}
        
        if not nIter == 0:
                print('Now optimizing with ADAM')
        
        start_time = time.time()
        for it in range(nIter):
            self.sess.run(self.train_op_Adam, tf_dict)
            
            # Print
            if it % 100 == 0:
                elapsed = time.time() - start_time
                loss_value = self.sess.run(self.loss, tf_dict)
                lambda_1_value = self.sess.run(self.lambda_1)
                lambda_2_value = np.exp(self.sess.run(self.lambda_2))
                
                # Adaptive const
                adaptive_constant_value = self.sess.run(self.adaptive_constant, tf_dict)
                self.adaptive_constant_val = adaptive_constant_value * (1.0 - self.beta) \
                                            + self.beta * self.adaptive_constant_val
                self.adaptive_constant_log.append(self.adaptive_constant_val)
                
                print('ADAM It: %d, Loss: %.3e, Lambda_1: %.3f, Lambda_2: %.6f, Adaptive_const: %.2f, Time: %.2f' % 
                      (it, loss_value, lambda_1_value, lambda_2_value, self.adaptive_constant_val, elapsed))
                start_time = time.time()
        
        print('Now optimizing with L-BFGS')
        self.optimizer.minimize(self.sess,
                                feed_dict = tf_dict,
                                fetches = [self.loss, self.lambda_1, self.lambda_2],
                                loss_callback = self.callback)
        
        
    def predict(self, X_star):
        
        tf_dict = {self.x_f_tf: X_star[:,0:1], self.t_f_tf: X_star[:,1:2], self.x_u_tf: X_star[:,0:1], self.t_u_tf: X_star[:,1:2]}
        
        u_star = self.sess.run(self.u_pred, tf_dict)
        f_star = self.sess.run(self.f_pred, tf_dict)
        
        return u_star, f_star

   
if __name__ == "__main__": 
     
    nu = 0.01/np.pi
    
    
    N_u = 32
    N_f = 25600 - N_u
    
    layers = [2, 20, 20, 20, 20, 20, 20, 20, 20, 1]
    
    data = scipy.io.loadmat('../Data/burgers_shock.mat')
    
    t = data['t'].flatten()[:,None]
    x = data['x'].flatten()[:,None]
    Exact = np.real(data['usol']).T
    
    X, T = np.meshgrid(x,t)
    
    X_star = np.hstack((X.flatten()[:,None], T.flatten()[:,None]))
    u_star = Exact.flatten()[:,None]              

    # Domain bounds
    lb = X_star.min(0)
    ub = X_star.max(0) 
    
    idx_u = np.random.choice(X_star.shape[0], N_u, replace=False)
    idx_f = np.random.choice(X_star.shape[0], N_f, replace=False)
    idx_f = np.concatenate([idx_u, idx_f])
    
    X_u_train = X_star[idx_u,:]
    u_train = u_star[idx_u,:]
    
    X_f_train = X_star[idx_f,:]
    
    # Select x,t
#    tmp  = xr.DataArray(data = Exact, coords=[t[:,0],x[:,0]], dims=['t','x'])
#    tmpX = xr.DataArray(data = X,     coords=[t[:,0],x[:,0]], dims=['t','x'])
#    tmpT = xr.DataArray(data = T,     coords=[t[:,0],x[:,0]], dims=['t','x'])
#    
#    x_vals = [-0.8, -0.5, -0.2, -0.1, 0.1, 0.2, 0.5, 0.8]
#    t_vals = [0.0, 0.25, 0.5, 0.75, 1.0]
#    
#    tmpnp  =  tmp.sel(x = x_vals, method='nearest').values.flatten()[:,None]
#    tmpnpX = tmpX.sel(x = x_vals, method='nearest').values.flatten()
#    tmpnpT = tmpT.sel(x = x_vals, method='nearest').values.flatten()
#    
##    tmpnp  =  tmp.sel(t = t_vals, method='nearest').values.flatten()[:,None]
##    tmpnpX = tmpX.sel(t = t_vals, method='nearest').values.flatten()
##    tmpnpT = tmpT.sel(t = t_vals, method='nearest').values.flatten()
#    
#    tmpnpXT = np.stack((tmpnpX,tmpnpT), axis=1)
#    
#    X_u_train = tmpnpXT
#    u_train   = tmpnp
    
#    X_f_train = np.concatenate((X_u_train, X_f_train), axis=0)
    
    
    #%%
    
    def unison_shuffle(a,b):
        assert len(a) == len(b)
        p = np.random.permutation(len(a))
        return a[p], b[p]
    
    X_u_train, u_train = unison_shuffle(X_u_train, u_train)
    np.random.shuffle(X_f_train)
    
    #%%
    start_time = time.time()
    ######################################################################
    ######################## Noiseless Data ###############################
    ######################################################################
    noise = 0.0            
    
    print('Training model with {} residual points and {} supervised points'.format(X_f_train.shape[0], X_u_train.shape[0]))
    model = PhysicsInformedNN(X_f_train, X_u_train, u_train, layers, lb, ub)
    model.train(10000)
    
    u_pred, f_pred = model.predict(X_star)
            
    error_u = np.linalg.norm(u_star-u_pred,2)/np.linalg.norm(u_star,2)
    
    U_pred = griddata(X_star, u_pred.flatten(), (X, T), method='cubic')
        
    lambda_1_value = model.sess.run(model.lambda_1)
    lambda_2_value = model.sess.run(model.lambda_2)
    lambda_2_value = np.exp(lambda_2_value)
    
    error_lambda_1 = np.abs(lambda_1_value - 1.0)*100
    error_lambda_2 = np.abs(lambda_2_value - nu)/nu * 100
    
    print('Error u: %e' % (error_u))    
    print('Error lambda_1: %.5f%%' % (error_lambda_1))                             
    print('Error lambda_2: %.5f%%' % (error_lambda_2))  
    
    
    ######################################################################
    ########################### Noisy Data ###############################
    ######################################################################
    noise = 0.01        
    u_train = u_train + noise*np.std(u_train)*np.random.randn(u_train.shape[0], u_train.shape[1])
        
    model = PhysicsInformedNN(X_f_train, X_u_train, u_train, layers, lb, ub)
    model.train(10000)
    
    u_pred, f_pred = model.predict(X_star)
        
    lambda_1_value_noisy = model.sess.run(model.lambda_1)
    lambda_2_value_noisy = model.sess.run(model.lambda_2)
    lambda_2_value_noisy = np.exp(lambda_2_value_noisy)
            
    error_lambda_1_noisy = np.abs(lambda_1_value_noisy - 1.0)*100
    error_lambda_2_noisy = np.abs(lambda_2_value_noisy - nu)/nu * 100
    
    print('Error lambda_1: %f%%' % (error_lambda_1_noisy))
    print('Error lambda_2: %f%%' % (error_lambda_2_noisy))                           
    
    print('Total training time was: {}s'.format(time.time()-start_time))
    
    #%%
    ######################################################################
    ############################# Plotting ###############################
    ######################################################################    
    
    fig, ax = newfig(1.0, 1.4)
    ax.axis('off')
    
    ####### Row 0: u(t,x) ##################    
    gs0 = gridspec.GridSpec(1, 2)
    gs0.update(top=1-0.06, bottom=1-1.0/3.0+0.06, left=0.15, right=0.85, wspace=0)
    ax = plt.subplot(gs0[:, :])
    
    h = ax.imshow(U_pred.T, interpolation='nearest', cmap='rainbow', 
                  extent=[t.min(), t.max(), x.min(), x.max()], 
                  origin='lower', aspect='auto')
    divider = make_axes_locatable(ax)
    cax = divider.append_axes("right", size="5%", pad=0.05)
    fig.colorbar(h, cax=cax)
    
    ax.plot(X_u_train[:,1], X_u_train[:,0], 'kx', label = 'Data (%d points)' % (u_train.shape[0]), markersize = 2, clip_on = False)
    
    line = np.linspace(x.min(), x.max(), 2)[:,None]
    ax.plot(t[25]*np.ones((2,1)), line, 'w-', linewidth = 1)
    ax.plot(t[50]*np.ones((2,1)), line, 'w-', linewidth = 1)
    ax.plot(t[75]*np.ones((2,1)), line, 'w-', linewidth = 1)
    
    ax.set_xlabel('$t$')
    ax.set_ylabel('$x$')
    ax.legend(loc='upper center', bbox_to_anchor=(1.0, -0.125), ncol=5, frameon=False)
    ax.set_title('$u(t,x)$', fontsize = 10)
    
    ####### Row 1: u(t,x) slices ##################    
    gs1 = gridspec.GridSpec(1, 3)
    gs1.update(top=1-1.0/3.0-0.1, bottom=1.0-2.0/3.0, left=0.1, right=0.9, wspace=0.5)
    
    ax = plt.subplot(gs1[0, 0])
    ax.plot(x,Exact[25,:], 'b-', linewidth = 2, label = 'Exact')       
    ax.plot(x,U_pred[25,:], 'r--', linewidth = 2, label = 'Prediction')
    ax.set_xlabel('$x$')
    ax.set_ylabel('$u(t,x)$')    
    ax.set_title('$t = 0.25$', fontsize = 10)
    ax.axis('square')
    ax.set_xlim([-1.1,1.1])
    ax.set_ylim([-1.1,1.1])
    
    ax = plt.subplot(gs1[0, 1])
    ax.plot(x,Exact[50,:], 'b-', linewidth = 2, label = 'Exact')       
    ax.plot(x,U_pred[50,:], 'r--', linewidth = 2, label = 'Prediction')
    ax.set_xlabel('$x$')
    ax.set_ylabel('$u(t,x)$')
    ax.axis('square')
    ax.set_xlim([-1.1,1.1])
    ax.set_ylim([-1.1,1.1])
    ax.set_title('$t = 0.50$', fontsize = 10)
    ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.35), ncol=5, frameon=False)
    
    ax = plt.subplot(gs1[0, 2])
    ax.plot(x,Exact[75,:], 'b-', linewidth = 2, label = 'Exact')       
    ax.plot(x,U_pred[75,:], 'r--', linewidth = 2, label = 'Prediction')
    ax.set_xlabel('$x$')
    ax.set_ylabel('$u(t,x)$')
    ax.axis('square')
    ax.set_xlim([-1.1,1.1])
    ax.set_ylim([-1.1,1.1])    
    ax.set_title('$t = 0.75$', fontsize = 10)
    
    ####### Row 3: Identified PDE ##################    
    gs2 = gridspec.GridSpec(1, 3)
    gs2.update(top=1.0-2.0/3.0, bottom=0, left=0.0, right=1.0, wspace=0.0)
    
    ax = plt.subplot(gs2[:, :])
    ax.axis('off')
    s1 = r'$\begin{tabular}{ |c|c| }  \hline Correct PDE & $u_t + u u_x - 0.0031831 u_{xx} = 0$ \\  \hline Identified PDE (clean data) & '
    s2 = r'$u_t + %.5f u u_x - %.7f u_{xx} = 0$ \\  \hline ' % (lambda_1_value, lambda_2_value)
    s3 = r'Identified PDE (1\% noise) & '
    s4 = r'$u_t + %.5f u u_x - %.7f u_{xx} = 0$  \\  \hline ' % (lambda_1_value_noisy, lambda_2_value_noisy)
    s5 = r'\end{tabular}$'
    s = s1+s2+s3+s4+s5
    ax.text(0.1,0.1,s)
        
    savefig('./figures/Burgers_identification')  
    



