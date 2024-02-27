#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

r"""Representations for different kinds of datasets."""

from __future__ import annotations

import warnings
from typing import Any, Dict, List, Optional, Union

import torch
from botorch.exceptions.errors import InputDataError, UnsupportedError
from botorch.utils.containers import BotorchContainer, SliceContainer
from torch import long, ones, Tensor


class SupervisedDataset:
    r"""Base class for datasets consisting of labelled pairs `(X, Y)`
    and an optional `Yvar` that stipulates observations variances so
    that `Y[i] ~ N(f(X[i]), Yvar[i])`.

    Example:

    .. code-block:: python

        X = torch.rand(16, 2)
        Y = torch.rand(16, 1)
        feature_names = ["learning_rate", "embedding_dim"]
        outcome_names = ["neg training loss"]
        A = SupervisedDataset(
            X=X,
            Y=Y,
            feature_names=feature_names,
            outcome_names=outcome_names,
        )
        B = SupervisedDataset(
            X=DenseContainer(X, event_shape=X.shape[-1:]),
            Y=DenseContainer(Y, event_shape=Y.shape[-1:]),
            feature_names=feature_names,
            outcome_names=outcome_names,
        )
        assert A == B
    """

    def __init__(
        self,
        X: Union[BotorchContainer, Tensor],
        Y: Union[BotorchContainer, Tensor],
        *,
        feature_names: List[str],
        outcome_names: List[str],
        Yvar: Union[BotorchContainer, Tensor, None] = None,
        validate_init: bool = True,
    ) -> None:
        r"""Constructs a `SupervisedDataset`.

        Args:
            X: A `Tensor` or `BotorchContainer` representing the input features.
            Y: A `Tensor` or `BotorchContainer` representing the outcomes.
            feature_names: A list of names of the features in `X`.
            outcome_names: A list of names of the outcomes in `Y`.
            Yvar: An optional `Tensor` or `BotorchContainer` representing
                the observation noise.
            validate_init: If `True`, validates the input shapes.
        """
        self._X = X
        self._Y = Y
        self._Yvar = Yvar
        self.feature_names = feature_names
        self.outcome_names = outcome_names
        if validate_init:
            self._validate()

    @property
    def X(self) -> Tensor:
        if isinstance(self._X, Tensor):
            return self._X
        return self._X()

    @property
    def Y(self) -> Tensor:
        if isinstance(self._Y, Tensor):
            return self._Y
        return self._Y()

    @property
    def Yvar(self) -> Optional[Tensor]:
        if self._Yvar is None or isinstance(self._Yvar, Tensor):
            return self._Yvar
        return self._Yvar()

    def _validate(
        self,
        validate_feature_names: bool = True,
        validate_outcome_names: bool = True,
    ) -> None:
        r"""Checks that the shapes of the inputs are compatible with each other.

        Args:
            validate_feature_names: By default, we validate that the length of
                `feature_names` matches the # of columns of `self.X`. If a
                particular dataset, e.g., `RankingDataset`, is known to violate
                this assumption, this can be set to `False`.
            validate_outcome_names: By default, we validate that the length of
                `outcomes_names` matches the # of columns of `self.Y`. If a
                particular dataset, e.g., `RankingDataset`, is known to violate
                this assumption, this can be set to `False`.
        """
        shape_X = self.X.shape
        if isinstance(self._X, BotorchContainer):
            shape_X = shape_X[: len(shape_X) - len(self._X.event_shape)]
        else:
            shape_X = shape_X[:-1]
        shape_Y = self.Y.shape
        if isinstance(self._Y, BotorchContainer):
            shape_Y = shape_Y[: len(shape_Y) - len(self._Y.event_shape)]
        else:
            shape_Y = shape_Y[:-1]
        if shape_X != shape_Y:
            raise ValueError("Batch dimensions of `X` and `Y` are incompatible.")
        if self.Yvar is not None and self.Yvar.shape != self.Y.shape:
            raise ValueError("Shapes of `Y` and `Yvar` are incompatible.")
        if validate_feature_names and len(self.feature_names) != self.X.shape[-1]:
            raise ValueError(
                "`X` must have the same number of columns as the number of "
                "features in `feature_names`."
            )
        if validate_outcome_names and len(self.outcome_names) != self.Y.shape[-1]:
            raise ValueError(
                "`Y` must have the same number of columns as the number of "
                "outcomes in `outcome_names`."
            )

    def __eq__(self, other: Any) -> bool:
        return (
            type(other) is type(self)
            and torch.equal(self.X, other.X)
            and torch.equal(self.Y, other.Y)
            and (
                other.Yvar is None
                if self.Yvar is None
                else other.Yvar is not None and torch.equal(self.Yvar, other.Yvar)
            )
            and self.feature_names == other.feature_names
            and self.outcome_names == other.outcome_names
        )


class FixedNoiseDataset(SupervisedDataset):
    r"""A SupervisedDataset with an additional field `Yvar` that stipulates
    observations variances so that `Y[i] ~ N(f(X[i]), Yvar[i])`.

    NOTE: This is deprecated. Use `SupervisedDataset` instead.
    Will be removed in a future release (~v0.11).
    """

    def __init__(
        self,
        X: Union[BotorchContainer, Tensor],
        Y: Union[BotorchContainer, Tensor],
        Yvar: Union[BotorchContainer, Tensor],
        feature_names: List[str],
        outcome_names: List[str],
        validate_init: bool = True,
    ) -> None:
        r"""Initialize a `FixedNoiseDataset` -- deprecated!"""
        warnings.warn(
            "`FixedNoiseDataset` is deprecated. Use `SupervisedDataset` instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(
            X=X,
            Y=Y,
            feature_names=feature_names,
            outcome_names=outcome_names,
            Yvar=Yvar,
            validate_init=validate_init,
        )


class RankingDataset(SupervisedDataset):
    r"""A SupervisedDataset whose labelled pairs `(x, y)` consist of m-ary combinations
    `x ∈ Z^{m}` of elements from a ground set `Z = (z_1, ...)` and ranking vectors
    `y {0, ..., m - 1}^{m}` with properties:

        a) Ranks start at zero, i.e. min(y) = 0.
        b) Sorted ranks are contiguous unless one or more ties are present.
        c) `k` ranks are skipped after a `k`-way tie.

    Example:

    .. code-block:: python

        X = SliceContainer(
            values=torch.rand(16, 2),
            indices=torch.stack([torch.randperm(16)[:3] for _ in range(8)]),
            event_shape=torch.Size([3 * 2]),
        )
        Y = DenseContainer(
            torch.stack([torch.randperm(3) for _ in range(8)]),
            event_shape=torch.Size([3])
        )
        feature_names = ["item_0", "item_1"]
        outcome_names = ["ranking outcome"]
        dataset = RankingDataset(
            X=X,
            Y=Y,
            feature_names=feature_names,
            outcome_names=outcome_names,
        )
    """

    def __init__(
        self,
        X: SliceContainer,
        Y: Union[BotorchContainer, Tensor],
        feature_names: List[str],
        outcome_names: List[str],
        validate_init: bool = True,
    ) -> None:
        r"""Construct a `RankingDataset`.

        Args:
            X: A `SliceContainer` representing the input features being ranked.
            Y: A `Tensor` or `BotorchContainer` representing the rankings.
            feature_names: A list of names of the features in X.
            outcome_names: A list of names of the outcomes in Y.
            validate_init: If `True`, validates the input shapes.
        """
        super().__init__(
            X=X,
            Y=Y,
            feature_names=feature_names,
            outcome_names=outcome_names,
            Yvar=None,
            validate_init=validate_init,
        )

    def _validate(self) -> None:
        super()._validate(validate_feature_names=False, validate_outcome_names=False)
        if len(self.feature_names) != self._X.values.shape[-1]:
            raise ValueError(
                "The `values` field of `X` must have the same number of columns as "
                "the number of features in `feature_names`."
            )

        Y = self.Y
        arity = self._X.indices.shape[-1]
        if Y.min() < 0 or Y.max() >= arity:
            raise ValueError("Invalid ranking(s): out-of-bounds ranks detected.")

        # Ensure that rankings are well-defined
        Y_sort = Y.sort(descending=False, dim=-1).values
        y_incr = ones([], dtype=long)
        y_prev = None
        for i, y in enumerate(Y_sort.unbind(dim=-1)):
            if i == 0:
                if (y != 0).any():
                    raise ValueError("Invalid ranking(s): missing zero-th rank.")
                y_prev = y
                continue

            y_diff = y - y_prev
            y_prev = y

            # Either a tie or next ranking when accounting for previous ties
            if not ((y_diff == 0) | (y_diff == y_incr)).all():
                raise ValueError("Invalid ranking(s): ranks not skipped after ties.")

            # Same as: torch.where(y_diff == 0, y_incr + 1, 1)
            y_incr = y_incr - y_diff + 1


class MultiTaskDataset(SupervisedDataset):
    """This is a multi-task dataset that is constructed from the datasets of
    individual tasks. It offers functionality to combine parts of individual
    datasets to construct the inputs necessary for the `MultiTaskGP` models.

    The datasets of individual tasks are allowed to represent different sets
    of features. When there are heterogeneous feature sets, calling
    `MultiTaskDataset.X` will result in an error.
    """

    def __init__(
        self,
        datasets: List[SupervisedDataset],
        target_outcome_name: str,
        task_feature_index: Optional[int] = None,
    ):
        """Construct a `MultiTaskDataset`.

        Args:
            datasets: A list of the datasets of individual tasks. Each dataset
                is expected to contain data for only one outcome.
            target_outcome_name: Name of the target outcome to be modeled.
            task_feature_index: If the task feature is included in the Xs of the
                individual datasets, this should be used to specify its index.
                If omitted, the task feature will be appended while concatenating Xs.
                If given, we sanity-check that the names of the task features
                match between all datasets.
        """
        self.datasets: Dict[str, SupervisedDataset] = {
            ds.outcome_names[0]: ds for ds in datasets
        }
        self.target_outcome_name = target_outcome_name
        self.task_feature_index = task_feature_index
        self._validate_datasets(datasets=datasets)
        self.feature_names = self.datasets[target_outcome_name].feature_names
        self.outcome_names = [target_outcome_name]

        # Check if the datasets have identical feature sets.
        self.has_heterogeneous_features = any(
            datasets[0].feature_names != ds.feature_names for ds in datasets[1:]
        )

    @classmethod
    def from_joint_dataset(
        cls,
        dataset: SupervisedDataset,
        task_feature_index: int,
        target_task_value: int,
        outcome_names_per_task: Optional[Dict[int, str]] = None,
    ) -> MultiTaskDataset:
        r"""Construct a `MultiTaskDataset` from a joint dataset that includes the
        data for all tasks with the task feature index.

        This will break down the joint dataset into individual datasets by the value
        of the task feature. Each resulting dataset will have its outcome name set
        based on `outcome_names_per_task`, with the missing values defaulting to
        `task_<task_feature>` (except for the target task, which will retain the
        original outcome name from the dataset).

        Args:
            dataset: The joint dataset.
            task_feature_index: The column index of the task feature in `dataset.X`.
            target_task_value: The value of the task feature for the target task
                in the dataset. The data for the target task is filtered according to
                `dataset.X[task_feature_index] == target_task_value`.
            outcome_names_per_task: Optional dictionary mapping task feature values
                to the outcome names for each task. If not provided, the auxiliary
                tasks will be named `task_<task_feature>` and the target task will
                retain the outcome name from the dataset.

        Returns:
            A `MultiTaskDataset` instance.
        """
        if len(dataset.outcome_names) > 1:
            raise UnsupportedError(
                "Dataset containing more than one outcome is not supported. "
                f"Got {dataset.outcome_names=}."
            )
        outcome_names_per_task = outcome_names_per_task or {}
        # Split datasets by task feature.
        datasets = []
        all_task_features = dataset.X[:, task_feature_index]
        for task_value in all_task_features.unique().long().tolist():
            default_name = (
                dataset.outcome_names[0]
                if task_value == target_task_value
                else f"task_{task_value}"
            )
            outcome_name = outcome_names_per_task.get(task_value, default_name)
            filter_mask = all_task_features == task_value
            new_dataset = SupervisedDataset(
                X=dataset.X[filter_mask],
                Y=dataset.Y[filter_mask],
                Yvar=dataset.Yvar[filter_mask] if dataset.Yvar is not None else None,
                feature_names=dataset.feature_names,
                outcome_names=[outcome_name],
            )
            datasets.append(new_dataset)
        # Return the new
        return cls(
            datasets=datasets,
            target_outcome_name=outcome_names_per_task.get(
                target_task_value, dataset.outcome_names[0]
            ),
            task_feature_index=task_feature_index,
        )

    def _validate_datasets(self, datasets: List[SupervisedDataset]) -> None:
        """Validates that:
        * Each dataset models only one outcome;
        * Each outcome is modeled by only one dataset;
        * The target outcome is included in the datasets;
        * The datasets do not model batched inputs;
        * The task feature names of the datasets all match;
        * Either all or none of the datasets specify Yvar.
        """
        if any(len(ds.outcome_names) > 1 for ds in datasets):
            raise UnsupportedError(
                "Datasets containing more than one outcome are not supported."
            )
        if len(self.datasets) != len(datasets):
            raise UnsupportedError(
                "Received multiple datasets for the same outcome. Each dataset "
                "must contain data for a unique outcome. Got datasets with "
                f"outcome names: {(ds.outcome_names for ds in datasets)}."
            )
        if self.target_outcome_name not in self.datasets:
            raise InputDataError(
                "Target outcome is not present in the datasets. "
                f"Got {self.target_outcome_name=} and datasets for "
                f"outcomes {list(self.datasets.keys())}."
            )
        if any(len(ds.X.shape) > 2 for ds in datasets):
            raise UnsupportedError(
                "Datasets modeling batched inputs are not supported."
            )
        if self.task_feature_index is not None:
            tf_names = [ds.feature_names[self.task_feature_index] for ds in datasets]
            if any(name != tf_names[0] for name in tf_names[1:]):
                raise InputDataError(
                    "Expected the names of the task features to match across all "
                    f"datasets. Got {tf_names}."
                )
        all_Yvars = [ds.Yvar for ds in datasets]
        is_none = [yvar is None for yvar in all_Yvars]
        # Check that either all or None of the Yvars exist.
        if not all(is_none) and any(is_none):
            raise UnsupportedError(
                "Expected either all or none of the datasets to have a Yvar. "
                "Only subset of datasets define Yvar, which is unsupported."
            )

    @property
    def X(self) -> Tensor:
        """Appends task features, if needed, and concatenates the Xs of datasets to
        produce the `train_X` expected by `MultiTaskGP` and subclasses.

        If appending the task features, 0 is reserved for the target task and the
        remaining tasks are populated with 1, 2, ..., len(datasets) - 1.
        """
        if self.has_heterogeneous_features:
            raise UnsupportedError(
                "Concatenating `X`s from datasets with heterogeneous feature sets "
                "is not supported."
            )
        all_Xs = []
        next_task = 1
        for outcome, ds in self.datasets.items():
            if self.task_feature_index is None:
                # Append the task feature index.
                if outcome == self.target_outcome_name:
                    task_feature = 0
                else:
                    task_feature = next_task
                    next_task = next_task + 1
                all_Xs.append(torch.nn.functional.pad(ds.X, (0, 1), value=task_feature))
            else:
                all_Xs.append(ds.X)
        return torch.cat(all_Xs, dim=0)

    @property
    def Y(self) -> Tensor:
        """Concatenates Ys of the datasets."""
        return torch.cat([ds.Y for ds in self.datasets.values()], dim=0)

    @property
    def Yvar(self) -> Optional[Tensor]:
        """Concatenates Yvars of the datasets if they exist."""
        all_Yvars = [ds.Yvar for ds in self.datasets.values()]
        return None if all_Yvars[0] is None else torch.cat(all_Yvars, dim=0)

    def get_dataset_without_task_feature(self, outcome_name: str) -> SupervisedDataset:
        """A helper for extracting the child datasets with their task features removed.

        If the task feature index is `None`, the dataset will be returned as is.

        Args:
            outcome_name: The outcome name for the dataset to extract.

        Returns:
            The dataset without the task feature.
        """
        dataset = self.datasets[outcome_name]
        if self.task_feature_index is None:
            return dataset
        indices = list(range(len(self.feature_names)))
        indices.pop(self.task_feature_index)
        return SupervisedDataset(
            X=dataset.X[..., indices],
            Y=dataset.Y,
            Yvar=dataset.Yvar,
            feature_names=[
                fn for i, fn in enumerate(dataset.feature_names) if i in indices
            ],
            outcome_names=[outcome_name],
        )


class ContextualDataset(SupervisedDataset):
    """This is a contextual dataset that is constructed from either a single
    dateset containing overall outcome or a list of datasets that each corresponds
    to a context breakdown.
    """

    def __init__(
        self,
        datasets: List[SupervisedDataset],
        parameter_decomposition: Dict[str, List[str]],
        metric_decomposition: Optional[Dict[str, List[str]]] = None,
    ):
        """Construct a `ContextualDataset`.

        Args:
            datasets: A list of the datasets of individual tasks. Each dataset
                is expected to contain data for only one outcome.
            parameter_decomposition: Dict from context name to list of feature
                names corresponding to that context.
            metric_decomposition: Context breakdown metrics. Keys are context names.
                Values are the lists of metric names belonging to the context:
                {'context1': ['m1_c1'], 'context2': ['m1_c2'],}.
        """
        self.datasets: Dict[str, SupervisedDataset] = {
            ds.outcome_names[0]: ds for ds in datasets
        }
        self.feature_names = datasets[0].feature_names
        self.outcome_names = list(self.datasets.keys())
        self.parameter_decomposition = parameter_decomposition
        self.metric_decomposition = metric_decomposition
        self._validate_datasets()
        self._validate_decompositions()
        self.context_buckets = self._extract_context_buckets()
        self.parameter_index_decomp = {
            c: [self.feature_names.index(i) for i in parameter_decomposition[c]]
            for c in self.context_buckets
        }

    @property
    def X(self) -> Tensor:
        return self.datasets[self.outcome_names[0]].X

    @property
    def Y(self) -> Tensor:
        """Concatenates the Ys from the child datasets to create the Y expected
        by LCEM model if there are multiple datasets; Or return the Y expected
        by LCEA model if there is only one dataset.
        """
        Ys = [ds.Y for ds in self.datasets.values()]
        if len(Ys) == 1:
            return Ys[0]
        else:
            return torch.cat(Ys, dim=-1)

    @property
    def Yvar(self) -> Tensor:
        """Concatenates the Yvars from the child datasets to create the Y expected
        by LCEM model if there are multiple datasets; Or return the Yvar expected
        by LCEA model if there is only one dataset.
        """
        Yvars = [ds.Yvar for ds in self.datasets.values()]
        if Yvars[0] is None:
            return None
        elif len(Yvars) == 1:
            return Yvars[0]
        else:
            return torch.cat(Yvars, dim=-1)

    def _extract_context_buckets(self) -> List[str]:
        """Determines the context buckets from the data, and sets the
        context_buckets attribute.

        If we have an outcome for each context, we will lists the contexts
        in the same order as the outcomes (i.e., the order of datasets).

        If there is a single outcome (aggregated across contexts), the context
        buckets are taken from the parameter decomposition.
        """
        if len(self.outcome_names) > 1:
            assert len(self.outcome_names) == len(
                self.metric_decomposition
            ), "Expected a single dataset, or one for each context bucket."
            context_buckets = []
            for outcome_name in self.outcome_names:
                for k, v in self.metric_decomposition.items():
                    if outcome_name in v:
                        context_buckets.append(k)
                        break
        else:
            context_buckets = list(self.parameter_decomposition.keys())
        return context_buckets

    def _validate_datasets(self) -> None:
        """Validation of given datasets.
        1. each dataset has same X.
        2. metric_decomposition is not None if there are multiple datasets.
        3. metric_decomposition contains all the outcomes in datasets.
        4. value keys of parameter decomposition and the keys of
        metric_decomposition match context buckets.
        5. Yvar is None for all, or not for all.
        """
        datasets = list(self.datasets.values())
        X = datasets[0].X
        Yvar_is_none = datasets[0].Yvar is None
        for dataset in datasets:
            if torch.equal(X, dataset.X) is not True:
                raise InputDataError("Require same X for context buckets")
            if (dataset.Yvar is None) != Yvar_is_none:
                raise InputDataError(
                    "Require Yvar to be specified for all buckets, or for none"
                )

        if len(datasets) > 1:
            if self.metric_decomposition is None:
                raise InputDataError(
                    "metric_decomposition must be provided when there are"
                    + " multiple datasets."
                )
        else:
            if self.metric_decomposition is not None:
                raise InputDataError(
                    "metric_decomposition is redundant when there is one "
                    + "dataset for overall outcome."
                )

    def _validate_decompositions(self) -> None:
        """Checks that the decompositions are valid.

        Raises:
            InputDataError: If any of the decompositions are invalid.
        """
        if self.metric_decomposition is not None:
            m = len(list(self.metric_decomposition.values())[0])
            existing_metrics = set()
            for v in self.metric_decomposition.values():
                if existing_metrics.intersection(list(v)):
                    raise InputDataError(
                        "metric_decomposition has same metric for multiple contexts."
                    )
                if len(v) != m or len(set(v)) != m:
                    raise InputDataError(
                        "All values in metric_decomposition must have the same length."
                    )
                existing_metrics.update(list(v))

            if set(self.metric_decomposition.keys()) != set(
                self.parameter_decomposition.keys()
            ):
                raise InputDataError(
                    "Keys of metric and parameter decompositions do not match."
                )

            all_metrics = []
            for m in self.metric_decomposition.values():
                all_metrics.extend(m)
            for outcome in self.outcome_names:
                if outcome not in all_metrics:
                    raise InputDataError(
                        f"{outcome} is missing in metric_decomposition."
                    )
