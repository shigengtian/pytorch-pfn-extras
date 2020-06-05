from pytorch_pfn_extras.dataset.tabular import tabular_dataset
from pytorch_pfn_extras.dataset.tabular import _utils


class _Transform(tabular_dataset.TabularDataset):
    def __init__(self, dataset, transforms):

        self._dataset = dataset
        # keys are derived from the transform signatures
        keys = set()
        for s, _ in transforms:
            keys.update(s[1])

        # There might be issues with keys order?
        self._keys = tuple(sorted(list(keys)))

        self._transforms = transforms

    def __len__(self):
        return len(self._dataset)

    @property
    def keys(self):
        return self._keys

    @property
    def mode(self):
        if not hasattr(self, "_mode"):
            self.get_examples([0], None)
        return self._mode

    def _find_candidate_transforms(self, key_indices):
        # TODO: memoize

        # Assume that all the registered transformations are
        # disjoint on the outputs
        transforms = []
        operands = set()
        # Look for the transforms that produce the
        # columns specified in the result
        # to avoid calculating uneeded columns
        for s, t in self._transforms:
            ops, res = s
            res_idx = _utils._as_key_indices(res, self._keys)
            # We only allow to execute transformations that will generate
            # the exact requested columns
            key_indices = list(key_indices)  # sometimes we get ranges
            contained = all([r in key_indices for r in res_idx])
            if (key_indices is None) or contained:
                # Now look the indices of the keys we need to fetch
                # from the original dataset to apply this transformation
                ops_idx = _utils._as_key_indices(ops, self._dataset.keys)
                operands.update(ops_idx)
                transforms.append((ops_idx, t, res_idx))
        return sorted(list(operands)), transforms

    def get_examples(self, indices, key_indices):
        if key_indices is None:
            key_indices = range(len(self._keys))

        ops_idx, transforms = self._find_candidate_transforms(key_indices)
        in_examples = self._dataset.get_examples(indices, ops_idx)
        out_examples = tuple([] for _ in key_indices)

        for in_example in zip(*in_examples):
            for t_op_idx, transform, t_res_idx in transforms:
                # The size of in_example might not be the same
                # for the transformations.
                # Suppose we have 5 dimensions, a, b, c, d, e
                # Trans 1 uses a, d and Trans 2 uses only c
                # the selection returns a 3 element array of (a,c,d)
                # where trans 1 needs elems 0 and 2 and trans 2 needs 1
                # So we need to select the inputs accordingly
                # Recalculate the ops indexes to reflect this
                inputs = [in_example[ops_idx.index(i)] for i in t_op_idx]
                if self._dataset.mode is tuple:
                    # Should be always be a tuple with the correct value
                    out_example = transform(*inputs)
                elif self._dataset.mode is dict:
                    # TODO fix the inputs
                    keys = [self._dataset.keys for i in ops_idx]
                    out_example = transform(
                        **dict(zip(keys, inputs))
                    )
                elif self._dataset.mode is None:
                    out_example = self.transform(*inputs)
                if isinstance(out_example, tuple):
                    if hasattr(self, "_mode") and self._mode is not tuple:
                        raise ValueError(
                            "transform must not change its return type"
                        )
                    self._mode = tuple
                    for col_index, key_index in enumerate(t_res_idx):
                        # t_res_idx should directly map the output, when
                        # all the outputs are covered this works but when
                        # we are slicing the outputs using key_indices
                        # the result key index needs to be recalculated
                        out_examples[key_indices.index(key_index)].append(
                            out_example[col_index])
                elif isinstance(out_example, dict):
                    if hasattr(self, "_mode") and self._mode is not dict:
                        raise ValueError(
                            "transform must not change its return type"
                        )
                    self._mode = dict
                    # TODO fix output index calc
                    for col_index, key_index in enumerate(key_indices):
                        out_examples[col_index].append(
                            out_example[self._keys[key_index]]
                        )
                else:
                    if hasattr(self, "_mode") and self._mode is not None:
                        raise ValueError(
                            "transform must not change its return type"
                        )
                    self._mode = None
                    out_example = (out_example,)
                    # TODO fix output index calc
                    for col_index, key_index in enumerate(key_indices):
                        out_examples[col_index].append(out_example[key_index])

        return out_examples

    def convert(self, data):
        return self._dataset.convert(data)


class _TransformBatch(tabular_dataset.TabularDataset):
    def __init__(self, dataset, keys, transform_batch):
        if not isinstance(keys, tuple):
            keys = (keys,)

        self._dataset = dataset
        self._keys = keys
        self._transform_batch = transform_batch

    def __len__(self):
        return len(self._dataset)

    @property
    def keys(self):
        return self._keys

    @property
    def mode(self):
        if not hasattr(self, "_mode"):
            self.get_examples([0], None)
        return self._mode

    def get_examples(self, indices, key_indices):
        if indices is None:
            len_ = len(self)
        elif isinstance(indices, slice):
            start, stop, step = indices.indices(len(self))
            len_ = len(range(start, stop, step))
        else:
            len_ = len(indices)

        if key_indices is None:
            key_indices = range(len(self._keys))

        in_examples = self._dataset.get_examples(indices, None)

        if self._dataset.mode is tuple:
            out_examples = self._transform_batch(*in_examples)
        elif self._dataset.mode is dict:
            out_examples = self._transform_batch(
                **dict(zip(self._dataset.keys, in_examples))
            )
        elif self._dataset.mode is None:
            out_examples = self._transform_batch(*in_examples)

        if isinstance(out_examples, tuple):
            if hasattr(self, "_mode") and self._mode is not tuple:
                raise ValueError(
                    "transform_batch must not change its return type"
                )
            self._mode = tuple
            if not all(len(col) == len_ for col in out_examples):
                raise ValueError(
                    "transform_batch must not change the length of data"
                )
            return tuple(out_examples[key_index] for key_index in key_indices)
        elif isinstance(out_examples, dict):
            if hasattr(self, "_mode") and self._mode is not dict:
                raise ValueError(
                    "transform_batch must not change its return type"
                )
            self._mode = dict
            if not all(len(col) == len_ for col in out_examples.values()):
                raise ValueError(
                    "transform_batch must not change the length of data"
                )
            return tuple(
                out_examples[self._keys[key_index]]
                for key_index in key_indices
            )
        else:
            if hasattr(self, "_mode") and self._mode is not None:
                raise ValueError(
                    "transform_batch must not change its return type"
                )
            self._mode = None
            out_examples = (out_examples,)
            if not all(len(col) == len_ for col in out_examples):
                raise ValueError(
                    "transform_batch must not change the length of data"
                )
            return tuple(out_examples[key_index] for key_index in key_indices)

    def convert(self, data):
        return self._dataset.convert(data)
