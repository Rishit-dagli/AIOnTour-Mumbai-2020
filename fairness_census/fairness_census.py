#!/usr/bin/env python
# coding: utf-8

# # Teaching your models to play fair
# 
# In this notebook you will use `fairlearn` and the Fairness dashboard to generate predictors for the Census dataset. This dataset is a classification problem - given a range of data about 32,000 individuals, predict whether their annual income is above or below $50,000 \$ $ per year.
# 
# For the purposes of this notebook, we will treat this as a loan decision problem. We will pretend that the label indicates whether or not each individual repaid a loan in the past. We will use the data to train a predictor to predict whether previously unseen individuals will repay a loan or not. The assumption is that the model predictions are used to decide whether an individual should be offered a loan.
# 
# We will first train a fairness-unaware predictor and show that it leads to unfair decisions under a specific notion of fairness called *demographic parity*. We then mitigate unfairness by applying the `GridSearch` algorithm from `fairlearn` package.

# ## Setup
# 
# We will first install `fairlearn`

# In[1]:


get_ipython().system('pip install fairlearn')


# We will import a few packages that would be required

# In[2]:


from fairlearn.reductions import GridSearch
from fairlearn.reductions import DemographicParity, ErrorRate

from sklearn import svm, neighbors, tree
from sklearn.preprocessing import LabelEncoder,StandardScaler
from sklearn.linear_model import LogisticRegression
import pandas as pd
import shap

import numpy as np

shap.initjs()


# ## Loading the data
# 
# For simplicity, we import the data set from the `shap` package, which contains the data in a cleaned format.

# In[3]:


X_raw, Y = shap.datasets.adult()
X_raw.head()


# ## Performing data transformations
# 
# We are going to treat the sex of each individual as a protected attribute (where 0 indicates female and 1 indicates male), and in this particular case we are going separate this attribute out and drop it from the main data. We then perform some standard data preprocessing steps to convert the data into a format suitable for the ML algorithms

# In[4]:


A = X_raw["Sex"]
X = X_raw.drop(labels=['Sex'],axis = 1)
X = pd.get_dummies(X)

sc = StandardScaler()
X_scaled = sc.fit_transform(X)
X_scaled = pd.DataFrame(X_scaled, columns=X.columns)

le = LabelEncoder()
Y = le.fit_transform(Y)


# Finally, we split the data into training and test sets:

# In[5]:


from sklearn.model_selection import train_test_split
X_train, X_test, Y_train, Y_test, A_train, A_test = train_test_split(X_scaled, 
                                                    Y, 
                                                    A,
                                                    test_size = 0.2,
                                                    random_state=0,
                                                    stratify=Y)

# Work around indexing bug
X_train = X_train.reset_index(drop=True)
A_train = A_train.reset_index(drop=True)
X_test = X_test.reset_index(drop=True)
A_test = A_test.reset_index(drop=True)

# Improve labels
A_test = A_test.map({ 0:"female", 1:"male"})


# ## Training a fairness-unaware predictor
# 
# To show the effect of `fairlearn` we will first train a standard ML predictor that does not incorporate fairness For speed of demonstration, we use a simple logistic regression estimator from `sklearn`:

# In[6]:


unmitigated_predictor = LogisticRegression(solver='liblinear', fit_intercept=True)

unmitigated_predictor.fit(X_train, Y_train)


# ## Assess Fairness
# 
# We can load this predictor into the Fairness dashboard, and examine how it is unfair

# We can load this predictor into the Fairness dashboard, and examine how it is unfair (there is a warning about AzureML since we are not yet integrated with that product):

# In[7]:


from fairlearn.widget import FairlearnDashboard
FairlearnDashboard(sensitive_features=A_test, sensitive_feature_names=['sex'],
                   y_true=Y_test,
                   y_pred={"unmitigated": unmitigated_predictor.predict(X_test)})


# Looking at the disparity in accuracy, we see that males have an error rate about three times greater than the females. More interesting is the disparity in opportunitiy - males are offered loans at three times the rate of females.
# 
# Despite the fact that we removed the feature from the training data, our predictor still discriminates based on sex. This demonstrates that simply ignoring a protected attribute when fitting a predictor rarely eliminates unfairness. There will generally be enough other features correlated with the removed attribute to lead to disparate impact.

# ## Mitigation with GridSearch
# 
# The `GridSearch` class in `fairlearn` implements a simplified version of the exponentiated gradient reduction of [Agarwal et al. 2018](https://arxiv.org/abs/1803.02453). The user supplies a standard ML estimator, which is treated as a blackbox. `GridSearch` works by generating a sequence of relabellings and reweightings, and trains a predictor for each.
# 
# For this example, we specify demographic parity (on the protected attribute of sex) as the fairness metric. Demographic parity requires that individuals are offered the opportunity (are approved for a loan in this example) independent of membership in the protected class (i.e., females and males should be offered loans at the same rate). We are using this metric for the sake of simplicity; in general, the appropriate fairness metric will not be obvious.

# In[8]:


sweep = GridSearch(LogisticRegression(solver='liblinear', fit_intercept=True),
                   constraints=DemographicParity(),
                   grid_size=71)


# Our algorithms provide `fit()` and `predict()` methods, so they behave in a similar manner to other ML packages in Python. We do however have to specify two extra arguments to `fit()` - the column of protected attribute labels, and also the number of predictors to generate in our sweep.
# 
# After `fit()` completes, we extract the full set of predictors from the `GridSearch` object.

# In[9]:


sweep.fit(X_train, Y_train,
          sensitive_features=A_train)


# In[10]:


predictors = sweep._predictors


# We could load these predictors into the Fairness dashboard now. However, the plot would be somewhat confusing due to their number. In this case, we are going to remove the predictors which are dominated in the error-disparity space by others from the sweep (note that the disparity will only be calculated for the protected attribute; other potentially protected attributes will not be mitigated). In general, one might not want to do this, since there may be other considerations beyond the strict optimisation of error and disparity (of the given protected attribute).

# In[11]:


errors, disparities = [], []
for m in predictors:
    classifier = lambda X: m.predict(X)
    
    error = ErrorRate()
    error.load_data(X_train, pd.Series(Y_train), sensitive_features=A_train)
    disparity = DemographicParity()
    disparity.load_data(X_train, pd.Series(Y_train), sensitive_features=A_train)
    
    errors.append(error.gamma(classifier)[0])
    disparities.append(disparity.gamma(classifier).max())
    
all_results = pd.DataFrame( {"predictor": predictors, "error": errors, "disparity": disparities})

non_dominated = []
for row in all_results.itertuples():
    errors_for_lower_or_eq_disparity = all_results["error"][all_results["disparity"]<=row.disparity]
    if row.error <= errors_for_lower_or_eq_disparity.min():
        non_dominated.append(row.predictor)


# Finally, we can put the dominant models into the Fairness dashboard, along with the unmitigated model.

# In[12]:


dashboard_predicted = {"unmitigated": unmitigated_predictor.predict(X_test)}
for i in range(len(non_dominated)):
    key = "dominant_model_{0}".format(i)
    value = non_dominated[i].predict(X_test)
    dashboard_predicted[key] = value


FairlearnDashboard(sensitive_features=A_test, sensitive_feature_names=['sex'],
                   y_true=Y_test,
                   y_pred=dashboard_predicted)


# We see a Pareto front forming - the set of predictors which represent optimal tradeoffs between accuracy and disparity i predictions. In the ideal case, we would have a predictor at (1,0) - perfectly accurate and without any unfairness under demographic parity (with respect to the protected attribute "sex"). The Pareto front represents the closest we can come to this ideal based on our data and choice of estimator. Note the range of the axes - the disparity axis covers more values than the accuracy, so we can reduce disparity substantially for a small loss in accuracy.
# 
# By clicking on individual models on the plot, we can inspect their metrics for disparity and accuracy in greater detail. In a real example, we would then pick the model which represented the best trade-off between accuracy and disparity given the relevant business constraints.
