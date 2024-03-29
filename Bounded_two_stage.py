import time
import numpy as np
from copy import deepcopy
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from deeprobust.graph.utils import accuracy
import matplotlib.pyplot as plt
import warnings
from utils import *

class RwlGNN:
    """ Rwl-GNN-Two-stage (Robust Weighted Gaph Laplacian)
    Parameters
    ----------
    model:
        model: The backbone GNN model in RWL-GNN
    args:
        model configs
    device: str
        'cpu' or 'cuda'.
    Examples
    --------
    See details in https://github.com/Bharat-Runwal/RWL-GNN.
    """

    def __init__(self,model, args, device):     #####################
        self.device = device
        self.args = args                            ##########################
    def fit(self, features, adj):
        """Train RWL-GNN: Two-Stage.
        Parameters
        ----------
        features :
            node features
        adj :
            the adjacency matrix. The format could be torch.tensor or scipy matrix
        labels :
            node labels
        idx_train :
            node training indices
        idx_val :
            node validation indices
        """
        print("Using bounded two stage ")
        args = self.args
        self.symmetric = args.symmetric

        optim_sgl = args.optim
        lr_sgl = args.lr_optim

        adj = (adj.t() + adj)/2
        rowsum = adj.sum(1)
        r_inv = rowsum.flatten()
        D = torch.diag(r_inv)
        L_noise = D - adj


        self.bound = args.bound            #############################
        self.d =  features.shape[1]        ################### Dimension of feature




        # INIT
        #n = features.shape[0]
        #self.weight = torch.rand(int(n*(n-1)/2),dtype=torch.float,requires_grad=True,device = self.device)

        self.weight = self.Linv(L_noise)  ###################################################
        self.w_old = torch.zeros_like(self.weight)  ####################  To store previous w value ( w^{t-1} )

        # self.w_old= self.Linv(L_noise)
        #self.w_old=self.weight

        self.weight.requires_grad = True
        self.weight = self.weight.to(self.device)
        self.w_old = self.w_old.to(self.device)  #######################################################
        c = self.Lstar(2*L_noise*args.alpha - args.beta*(torch.matmul(features,features.t())) )

        #sq_norm_Aw = torch.norm(self.A(), p="fro") ** 2   ############################################################

        #new_term = self.bound * (2 * self.Astar(self.A()) - self.w_old) / (sq_norm_Aw - self.w_old.t() * self.weight)  ######################
        #print(f'New Term sum = {new_term.sum()}')

        #k = self.Astar(self.A())-self.w_old
        #kk = sq_norm_Aw - self.w_old.t()*self.weight


        #print(f'c = {c.shape}')
        #print(f'self.Astar(self.A())-self.w_old) = {k.sum()}')
        #print(f'sq_norm_Aw - self.w_old.t()*self.weight) = {kk.sum()}')

        if optim_sgl == "Adam":
            self.sgl_opt =AdamOptimizer(self.weight,lr=lr_sgl)
        elif optim_sgl == "RMSProp":
            self.sgl_opt = RMSProp(self.weight,lr = lr_sgl)
        elif optim_sgl == "sgd_momentum":
            self.sgl_opt = sgd_moment(self.weight,lr=lr_sgl)
        else:
            self.sgl_opt = sgd(self.weight,lr=lr_sgl) 

        t_total = time.time()
        
        for epoch in range(args.epochs_pre):

            sq_norm_Aw = torch.norm(self.A(), p="fro")**2
            new_term = self.bound * (2 * self.Astar(self.A()) - self.w_old) / (sq_norm_Aw - self.w_old.t() * self.weight) ##

            self.train_specific(c,new_term)
            if epoch%20==0:
                ##kk = sq_norm_Aw - self.w_old.t() * self.weight
                bound_loss = self.bound**2 * torch.log(torch.sqrt(torch.tensor(self.d))*torch.square(torch.norm(self.A()-self.A(self.w_old))))
                loss_fro = args.alpha * torch.norm(self.L() - L_noise, p='fro')
                loss_smooth_feat = args.beta * self.feature_smoothing(self.A(), features)

                print(f'Total loss = {loss_fro+loss_smooth_feat}, Bound loss = {bound_loss}')
                #print(f'sq_norm_Aw - self.w_old.t()*self.weight) = {kk.sum()}')
                #print(f'New Term sum = {new_term.sum()}')
  
        print("Optimization Finished!")
        print("Total time elapsed: {:.4f}s".format(time.time() - t_total))
        print(args)
        print(f'New term sum = {new_term.sum()}')

        return self.A().detach()



    def w_grad(self,alpha,c,new_term):
      with torch.no_grad():
        grad_f = self.Lstar(alpha*self.L()) - c + new_term
      
        return grad_f


    def train_specific(self,c,new_term):
        args = self.args
        if args.debug:
            print("\n=== train_adj ===")
        t = time.time()
              
        sgl_grad = self.w_grad(args.alpha ,c,new_term) ###########################################

        self.w_old = self.weight

        total_grad  = sgl_grad  
        self.weight = self.sgl_opt.backward_pass(total_grad)
        self.weight = torch.clamp(self.weight,min=0)



    def feature_smoothing(self, adj, X):
        adj = (adj.t() + adj)/2
        rowsum = adj.sum(1)
        r_inv = rowsum.flatten()
        D = torch.diag(r_inv)
        L = D - adj

        r_inv = r_inv  + 1e-3
        r_inv = r_inv.pow(-1/2).flatten()
        r_inv[torch.isinf(r_inv)] = 0.
        r_mat_inv = torch.diag(r_inv)
        L = r_mat_inv @ L @ r_mat_inv

        XLXT = torch.matmul(torch.matmul(X.t(), L), X)
        loss_smooth_feat = torch.trace(XLXT)
        return loss_smooth_feat


    def A(self,weight=None):
        # with torch.no_grad():
        if weight == None:
            k = self.weight.shape[0]
            a = self.weight
        else:
            k = weight.shape[0]
            a = weight
        n = int(0.5 * (1 + np.sqrt(1 + 8 * k)))
        Aw = torch.zeros((n,n),device=self.device)
        b=torch.triu_indices(n,n,1)
        Aw[b[0],b[1]] =a
        Aw = Aw + Aw.t()
        return Aw

    def Astar(self,adjacency):
        n = adjacency.shape[0]
        k = n * (n - 1) // 2
        weight = torch.zeros(k,device= self.device)
        b = torch.triu_indices(n, n, 1)
        weight = adjacency[b[0], b[1]]
        return weight


    def L(self,weight=None):
        if weight==None:
            k= len(self.weight)
            a = self.weight 
        else:
            k = len(weight)
            a = weight
        n = int(0.5*(1+ np.sqrt(1+8*k)))
        Lw = torch.zeros((n,n),device=self.device)
        b=torch.triu_indices(n,n,1)
        Lw[b[0],b[1]] = -a  
        Lw = Lw + Lw.t()
        row,col = np.diag_indices_from(Lw)
        Lw[row,col] = -Lw.sum(axis=1)
        return Lw     



    def Linv(self,M):
      with torch.no_grad():
        N=M.shape[0]
        k=int(0.5*N*(N-1))
        # l=0
        w=torch.zeros(k,device=self.device)
        ##in the triu_indices try changing the 1 to 0/-1/2 for other
        ## ascpect of result on how you want the diagonal to be included
        indices=torch.triu_indices(N,N,1)
        M_t=torch.tensor(M)
        w=-M_t[indices[0],indices[1]]
        return w

    def Lstar(self,M):
        N = M.shape[1]
        k =int( 0.5*N*(N-1))
        w = torch.zeros(k,device=self.device)
        tu_enteries=torch.zeros(k,device=self.device)
        tu=torch.triu_indices(N,N,1)
        tu_enteries=M[tu[0],tu[1]]

        diagonal_enteries=torch.diagonal(M)
        b_diagonal=diagonal_enteries[0:N-1]
        x=torch.linspace(N-1,1,steps=N-1,dtype=torch.long,device=self.device)
        x_r = x[:N]
        diagonal_enteries_a=torch.repeat_interleave(b_diagonal,x_r)
        new_arr=torch.tile(diagonal_enteries,(N,1))
        tu_new=torch.triu_indices(N,N,1)
        diagonal_enteries_b=new_arr[tu_new[0],tu_new[1]]
        w=diagonal_enteries_a+diagonal_enteries_b-2*tu_enteries
   
        return w



    def normalize(self,w=None):

        if self.symmetric:
            if w == None:
                adj = (self.A() + self.A().t())
            else:
                adj = self.A(w)
            
            adj = adj + adj.t()
        else:
            if w == None:
                adj = self.A()
            else:
                adj = self.A(w)

        normalized_adj = self._normalize(adj + torch.eye(adj.shape[0]).to(self.device))
        return normalized_adj

    def _normalize(self, mx):
        rowsum = mx.sum(1)
        r_inv = rowsum.pow(-1/2).flatten()
        r_inv[torch.isinf(r_inv)] = 0.
        r_mat_inv = torch.diag(r_inv)
        mx = r_mat_inv @ mx
        mx = mx @ r_mat_inv
        return mx
