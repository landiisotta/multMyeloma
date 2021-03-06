##Import libraries

import os
import csv
from collections import OrderedDict
import random
import numpy as np
import sys

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils import data
from torch.utils.data import Dataset, DataLoader

from sklearn.manifold.t_sne import TSNE

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

##DEEP LEARNING ARCHITECTURE 
##N = batch_size
##s = medical term sequence length
##d = embedding dimension
##C = channels for convolution

class ehrModel(nn.Module):

        def __init__(self, vocab_size, emb_dim, kernel_size):
                super(ehrModel, self).__init__()

                self.vocab_size = vocab_size
                self.emb_dim = emb_dim
                self.kernel_size = kernel_size

                self.ch_l1 = int(emb_dim / 2)
                self.ch_l2 = int(self.ch_l1 / 2)
                self.padding = int((kernel_size - 1) / 2)

                self.embedding = nn.Embedding(vocab_size, emb_dim)
                self.cnn_l1 = nn.Conv1d(emb_dim, self.ch_l1, kernel_size=kernel_size, padding=self.padding)
                self.cnn_l2 = nn.Conv1d(self.ch_l1, self.ch_l2, kernel_size=kernel_size, padding=self.padding)
                self.linear1 = nn.Linear(self.ch_l2 * 92, 64)
                self.linear2 = nn.Linear(64, vocab_size * 100)

        def forward(self, x):
                input_vect = x ##[N, s]

                embeds = self.embedding(x) ##Input: [N, s]; Output: [N, s, d]
                embeds = embeds.permute(0,2,1)
                
                out = F.relu(self.cnn_l1(embeds)) ##[N, C, s]
                out = F.max_pool1d(out, stride=1, kernel_size=kernel_size) ##[N, C/2, s-(kernel_size-1)]
                out = self.cnn_l2(out) ##[N, C/2, s-(kernel_size-1)]
                out = F.max_pool1d(out, stride=1, kernel_size=kernel_size) ##[N, C/4, s-(kernel_size-1)]
                out = F.relu(out) ##[N, C', s'] where C'=C/4 and s' = s-2(kernel_size-1)
      
                out = out.view(-1, out.shape[2] * out.shape[1]) ##[N, C'*s']
                out = self.linear1(out)
                encoded_vect = out
                out = F.relu(out)
                out = self.linear2(out)
                out = out.view(-1, vocab_size, input_vect.shape[1])

                return(out, encoded_vect)


#DATA PREPARATION
DATA_PATH = os.path.expanduser("~/data1/multMyeloma/data/")
PLOT_PATH = os.path.expanduser("~/data1/multMyeloma/plot")

##class MMData and mycollate method
class MMdata(Dataset):
        def __init__(self, list_mrn, dic_seq):
                self.list_mrn = list_mrn
                self.dic_seq = dic_seq

        def __getitem__(self, index):
                mrn_idx = self.list_mrn[index]
                sequence = self.dic_seq[mrn_idx]
                return sequence, mrn_idx

        def __len__(self):
                return len(self.list_mrn)

def my_collate(batch):
        data = []
        target = []
        for item in batch:
                idx = random.sample(range(len(item[0])), 1)[0]
                data.append(item[0][idx])
                target += [item[1]]
        data = torch.tensor(data, dtype=torch.long)
        return [data, target]

##Read the dictionaries of medical terms
with open(os.path.join(DATA_PATH, 'rxnorm_to_ix.csv'), 'r') as infile:
    file = csv.reader(infile, delimiter=',')
    rxnorm_to_ix = {}
    for el in file:
        rxnorm_to_ix[el[0]] = int(el[1])

with open(os.path.join(DATA_PATH, 'ix_to_rxnorm.csv'), 'r') as infile:
    file = csv.reader(infile, delimiter=',')
    ix_to_rxnorm = {}
    for el in file:
        ix_to_rxnorm[int(el[0])] = el[1]

##Read the data sequences
with open(os.path.join(DATA_PATH, 'patient_sequences.csv'), 'r', newline='') as f2:
    rows = csv.reader(f2, delimiter=',', quotechar='"')
    patient_sequences = OrderedDict()
    for row in rows:
        if row[0] not in patient_sequences:
            patient_sequences[row[0]] = [[int(row[i]) for i in range(1,len(row))]]
        else:
            patient_sequences[row[0]] += [[int(row[i]) for i in range(1,len(row))]]


##create training and test dictionaries
random.seed(42)
traintest = {'train':[], 'test':[]}
mrn_len = len([key for key in patient_sequences.keys()])
mrn_list = list(patient_sequences.keys())
test_idx = random.sample(range(0, mrn_len), int(mrn_len * 0.2))

for i in range(0, mrn_len):
    if i in test_idx:
        traintest['test'].append(mrn_list[i])
    else:
        traintest['train'].append(mrn_list[i])

# By default, each worker will have its PyTorch seed set to base_seed + worker_id, 
##where base_seed is a long generated by main process using its RNG. 
max_epochs = 200

torch.manual_seed(12)
torch.cuda.manual_seed(12)
np.random.seed(12)
random.seed(12)

training_set, test_set = MMdata(traintest['train'], patient_sequences), MMdata(traintest['test'], patient_sequences)
training_generator = data.DataLoader(training_set, batch_size=32, shuffle=True, collate_fn=my_collate)
test_generator = data.DataLoader(test_set, batch_size=32, shuffle=False, collate_fn=my_collate)

vocab_size = len(rxnorm_to_ix)
embedding_dim = 128
kernel_size = 5

#torch.cuda.set_device(1)

model = ehrModel(vocab_size, embedding_dim, kernel_size)
model.cuda()

criterion = nn.CrossEntropyLoss()

CURR_LR = 0.001
optimizer = torch.optim.Adam(model.parameters(), lr=CURR_LR, weight_decay=1e-5)
losses_tr = []
losses_ts = []
perplexity_tr = []
perplexity_ts = []

minimum_loss = -1
last_loss_val = -1


##TRAINING
model.train()

for epoch in range(max_epochs):
        loss_trTmp = 0.0
        #perplexity_trTmp = 0.0
        for idx, (batch, mrns) in enumerate(training_generator):
                batch = batch.cuda()
                optimizer.zero_grad()
                out, encoded_vect = model(batch)
                loss = criterion(out, batch)
                #print("Loss value:{0}".format(loss))
                loss.backward()
                optimizer.step()

                loss_trTmp += loss.item()
                #perplexity_trTmp += perplexity(out)

        loss_trTmp = loss_trTmp/(idx+1)
        #perplexity_trTmp = perplexity_trTmp/len(training_generator.dataset)
        losses_tr.append(loss_trTmp)
        #perplexity_tr.append(perplexity_trTmp)

        print("epoch:{0} - loss:{1:.5f}".format(epoch, loss_trTmp))


##TESTING
model.cpu()
model.eval()
encoded_data = []

with torch.no_grad():
    loss_tsTmp = 0.0
    #perplexity_tsTmp = 0.0
    for idx, (batch, mrns) in enumerate(test_generator):
       	out, encoded_vect = model(batch)
       	loss = criterion(out, batch)
       	loss_tsTmp += loss.item()
        #perplexity_tsTmp += perplexity(out)
        encoded_data += (encoded_vect.tolist())
    loss_tsTmp = loss_tsTmp/(idx + 1)
    #perplexity_tsTmp = perplexity_tsTmp/len(test_generator.dataset)
    #perplexity_ts.append(perplexity_tsTmp)

    print("lossTS:{0:.5f}".format(loss_tsTmp))


##LOSS plot
blue_patch = mpatches.Patch(color='blue', label='training')
orange_patch = mpatches.Patch(color='orange', label='test')

plt.figure(figsize=(20,10))
plt.plot(losses_tr, color='blue')
#plt.plot(losses_ts, color='orange')
plt.legend(handles=[blue_patch, orange_patch], prop={'size':'15'})
plt.savefig(os.path.join(PLOT_PATH, "lossTR.jpg"))

##T-SNE
#encoded = encoded_vect.detach().cpu().numpy()
tsne = TSNE(n_components=2, n_iter=5000, perplexity=2)
X_tsne = tsne.fit_transform(encoded_data)

plt.figure(figsize=(20,10))
plt.scatter(X_tsne[:,0], X_tsne[:,1])
plt.savefig(os.path.join(PLOT_PATH, "tSNEtr.jpg"))