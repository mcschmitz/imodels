from copy import deepcopy
from typing import List

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import sklearn.datasets
from sklearn import datasets
from sklearn import tree
from sklearn.base import BaseEstimator, ClassifierMixin, RegressorMixin
from sklearn.model_selection import train_test_split, cross_val_score
from sklearn.tree import plot_tree, DecisionTreeClassifier
from sklearn.utils import check_X_y, check_array
from sklearn.utils.validation import _check_sample_weight
from figs import Node
from imodels.tree.viz_utils import extract_sklearn_tree_from_figs
from imodels import FIGSClassifier
from imodels import FIGSRegressor


class D_FIGS(FIGSRegressor):
    # Needs to store the old X and y

    feature_phases = None

    def __init__(self, max_rules: int = 12, min_impurity_decrease: float = 0.0, random_state=None,
                 max_features: str = None):
        super().__init__(max_rules, min_impurity_decrease, random_state, max_features)
        # self.feature_phases = feature_phases
        # , feature_phases = {}

    def check_phase(self, old_phases, new_phase):
        for i in range(len(old_phases)):
            '''
            phase 2 features can be available (not NA) only if all phase 1 features are available
            '''
            if np.isnan(old_phases).any() and not np.isnan(new_phase).all():
                raise ValueError('A very specific bad thing happened.')

    '''
    add the new phase features to X, delete samples that has NaN in new_phase potentially refit the model?
    '''

    def add_new_phase(self, new_phase):
        self.check_phase(self.old_phase, new_phase)
        concatenated_phase = np.concatenate((self.old_phase, new_phase), axis=0)
        old_phase = concatenated_phase

        # after getting the copied model and potential splits, change the idx
        for node in self.potential_splits:
            new_idx = []
            for i in range(len(node.idx)):
                new_feature = new_phase[node.idx[i]]  # new phase features for the particular sample i
                if not np.isnan(new_feature).any():  # If the new phase has no nan
                    new_idx.append(node.idx[i])
            node.idx = new_idx  # The leaves that we can potentially split on now contain only samples with new_phase

    def extend_trees(self, X, y, max_rules=5):
        # Need to add max_rules each time so that it's bigger than the complexity
        self.max_rules += max_rules
        all_leaves = []
        potential_splits = []
        y_predictions_per_tree = {}  # predictions for each tree
        y_residuals_per_tree = {}  # based on predictions above

        # Get all the leaves from the previous model
        for node in self.trees_:
            all_leaves += self.get_leaves(node)
        # iterate through all the leaves and split them on the new feature
        for leaf in all_leaves:
            potential_split = self._construct_node_with_stump(X, y, idxs=leaf.idxs, tree_num=leaf.tree_num, max_features=None)
            if potential_split.impurity_reduction is not None:
                # Update the leaves on the previous model
                leaf.setattrs(feature=potential_split.feature,
                              threshold=potential_split.threshold,
                              impurity_reduction=potential_split.impurity_reduction,
                              left_temp=potential_split.left_temp,
                              right_temp=potential_split.right_temp,
                              tree_num=potential_split.tree_num,
                              impurity=potential_split.impurity,
                              idx=potential_split.idxs)
                # Add to the potential splits, and do the same fitting process as in the fig
                potential_splits.append(leaf)
        for i in potential_splits:
            print(i.impurity_reduction)
        potential_splits = sorted(potential_splits, key=lambda x: x.impurity_reduction)
        finished = False
        while len(potential_splits) > 0 and not finished:
            # print('potential_splits', [str(s) for s in potential_splits])
            split_node = potential_splits.pop()  # get node with max impurity_reduction (since it's sorted)

            # don't split on node
            if split_node.impurity_reduction < self.min_impurity_decrease:
                finished = True
                break

            # split on node
            self.complexity_ += 1

            # if added a tree root
            if split_node.is_root:

                # start a new tree
                self.trees_.append(split_node)

                # update tree_num
                for node_ in [split_node, split_node.left_temp, split_node.right_temp]:
                    if node_ is not None:
                        node_.tree_num = len(self.trees_) - 1

                # add new root potential node
                node_new_root = Node(is_root=True, idxs=np.ones(X.shape[0], dtype=bool),
                                     tree_num=-1)
                potential_splits.append(node_new_root)

            # add children to potential splits
            # assign left_temp, right_temp to be proper children
            # (basically adds them to tree in predict method)
            split_node.setattrs(left=split_node.left_temp, right=split_node.right_temp)

            # add children to potential_splits
            potential_splits.append(split_node.left)
            potential_splits.append(split_node.right)

            # update predictions for altered tree
            for tree_num_ in range(len(self.trees_)):
                y_predictions_per_tree[tree_num_] = self._predict_tree(self.trees_[tree_num_], X)
            y_predictions_per_tree[-1] = np.zeros(X.shape[0])  # dummy 0 preds for possible new trees

            # update residuals for each tree
            # -1 is key for potential new tree
            for tree_num_ in list(range(len(self.trees_))) + [-1]:
                y_residuals_per_tree[tree_num_] = deepcopy(y)

                # subtract predictions of all other trees
                for tree_num_other_ in range(len(self.trees_)):
                    if not tree_num_other_ == tree_num_:
                        y_residuals_per_tree[tree_num_] -= y_predictions_per_tree[tree_num_other_]

            # recompute all impurities + update potential_split children
            potential_splits_new = []
            for potential_split in potential_splits:
                y_target = y_residuals_per_tree[potential_split.tree_num]

                # re-calculate the best split
                potential_split_updated = self._construct_node_with_stump(X=X,
                                                                          y=y_target,
                                                                          idxs=potential_split.idxs,
                                                                          tree_num=potential_split.tree_num,
                                                                          max_features=self.max_features)

                # need to preserve certain attributes from before (value at this split + is_root)
                # value may change because residuals may have changed, but we want it to store the value from before
                potential_split.setattrs(
                    feature=potential_split_updated.feature,
                    threshold=potential_split_updated.threshold,
                    impurity_reduction=potential_split_updated.impurity_reduction,
                    left_temp=potential_split_updated.left_temp,
                    right_temp=potential_split_updated.right_temp,
                )

                # this is a valid split
                if potential_split.impurity_reduction is not None:
                    potential_splits_new.append(potential_split)

            # sort so largest impurity reduction comes last (should probs make this a heap later)
            potential_splits = sorted(potential_splits_new, key=lambda x: x.impurity_reduction)
            if self.max_rules is not None and self.complexity_ >= self.max_rules:
                finished = True
                break

            # annotate final tree with node_id and value_sklearn
        for tree_ in self.trees_:
            node_counter = iter(range(0, int(1e06)))

            def _annotate_node(node: Node, X, y):
                if node is None:
                    return

                # TODO does not incorporate sample weights
                value_counts = pd.Series(y).value_counts()
                try:
                    neg_count = value_counts[0.0]
                except KeyError:
                    neg_count = 0

                try:
                    pos_count = value_counts[1.0]
                except KeyError:
                    pos_count = 0

                value_sklearn = np.array([neg_count, pos_count], dtype=float)

                node.setattrs(node_id=next(node_counter), value_sklearn=value_sklearn)

                idxs_left = X[:, node.feature] <= node.threshold
                _annotate_node(node.left, X[idxs_left], y[idxs_left])
                _annotate_node(node.right, X[~idxs_left], y[~idxs_left])

            _annotate_node(tree_, X, y)
            return self

    def get_leaves(self, root):
        s1 = []
        s2 = []
        s1.append(root)
        while len(s1) != 0:
            curr = s1.pop()
            if curr.left:
                s1.append(curr.left)
            if curr.right:
                s1.append(curr.right)
            elif not curr.left and not curr.right:
                s2.append(curr)
        return s2



