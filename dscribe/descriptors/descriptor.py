from abc import ABCMeta, abstractmethod
from future.utils import with_metaclass

import numpy as np

from scipy.sparse import coo_matrix

from ase import Atoms
from dscribe.core.system import System
from dscribe.utils.species import get_atomic_numbers

from joblib import Parallel, delayed, parallel_backend


class Descriptor(with_metaclass(ABCMeta)):
    """An abstract base class for all descriptors.
    """

    def __init__(self, flatten, sparse):
        """
        Args:
            flatten (bool): Whether the output of create() should be flattened
                to a 1D array.
        """
        self._sparse = sparse
        self._flatten = flatten
        self._atomic_numbers = None
        self._atomic_number_set = None
        self._species = None

    @abstractmethod
    def create(self, system, *args, **kwargs):
        """Creates the descriptor for the given systems.

        Args:
            system (ase.Atoms): The system for which to create the descriptor.
            args: Descriptor specific positional arguments.
            kwargs: Descriptor specific keyword arguments.

        Returns:
            np.array | scipy.sparse.coo_matrix: A descriptor for the system.
        """

    @abstractmethod
    def get_number_of_features(self):
        """Used to inquire the final number of features that this descriptor
        will have.

        Returns:
            int: Number of features for this descriptor.
        """

    def get_system(self, system):
        """Used to convert the given atomic system into a custom System-object
        that is used internally. The System class inherits from ase.Atoms, but
        includes built-in caching for geometric quantities that may be re-used
        by the descriptors.

        Args:
            system (:class:`ase.Atoms` | :class:`.System`): Input system.

        Returns:
            :class:`.System`: The given system transformed into a corresponding
                System-object.
        """
        if isinstance(system, Atoms):
            if type(system) == System:
                return system
            else:
                return System.from_atoms(system)
        else:
            raise ValueError(
                "Invalid system with type: '{}'.".format(type(system))
            )

    def _set_species(self, species):
        """Used to setup the species information for this descriptor. This
        information includes an ordered list of unique atomic numbers, a set
        of atomic numbers and the original variable contents.

        Args:
            species(iterable): Chemical species either as a list of atomic
                numbers or list of chemical symbols.
        """
        # The species are stored as atomic numbers for internal use.
        atomic_numbers = get_atomic_numbers(species)
        self._atomic_numbers = atomic_numbers
        self._atomic_number_set = set(self._atomic_numbers)
        self._species = species

    def get_species_definition(self, species, atomic_numbers):
        """A convenience function that is included to decide the correct source
        of species information.

        Args:
            species(iterable): Species information either as atomic numbers or
                chemical symbols.
            atomic_numbers(iterable): Species information as atomic numbers

        Returns:
            The correct variable for the species information.

        Raises:
            ValueError: If both or none of the species information is defined.
        """
        # First check that the chemical species are defined either as number or
        # symbols
        if atomic_numbers is None and species is None:
            raise ValueError(
                "Please provide the atomic species either as chemical symbols "
                "or as atomic numbers."
            )
        elif atomic_numbers is not None and species is not None:
            raise ValueError(
                "Both species and atomic numbers provided. Please only provide"
                "either one."
            )

        if atomic_numbers is not None:
            return atomic_numbers
        else:
            return species

    def check_atomic_numbers(self, atomic_numbers):
        """Used to check that the given atomic numbers have been defined for
        this descriptor.

        Args:
            species(iterable): Atomic numbers to check.

        Raises:
            ValueError: If the atomic numbers in the given system are not
            included in the species given to this descriptor.
        """
        # Check that the system does not have elements that are not in the list
        # of atomic numbers
        zs = set(atomic_numbers)
        if not zs.issubset(self._atomic_number_set):
            raise ValueError(
                "The given system has the following atomic numbers not defined "
                "for this descriptor: {}"
                .format(zs.difference(self._atomic_number_set))
            )

    def create_parallel(self, inp, func, n_jobs, output_sizes=None, verbose=False, backend="loky"):
        """Used to parallelize the descriptor creation across multiple systems.

        Args:
            inp(list): Contains a tuple of input arguments for each processed
                system. These arguments are fed to the function specified by
                "func".
            func(function): Function that outputs the descriptor when given
                input arguments from "inp".
            n_jobs (int): Number of parallel jobs to instantiate. Parallellizes
                the calculation across samples. Defaults to serial calculation
                with n_jobs=1.
            output_sizes(list of ints): The size of the output for each job.
                Makes the creation faster by preallocating the correct amount of
                memory beforehand. If not specified, a dynamically created list of
                outputs is used.
            verbose(bool): Controls whether to print the progress of each job
                into to the console.
            backend (str): The parallelization method. Valid options are:

                * "threading": Parallelization based on threads. Has bery low
                memory and initialization overhead. Performance is limited by
                the amount of pure python code that needs to run. Ideal when
                most of the calculation time is used by C/C++ extensions that
                release the GIL.
                * "multiprocessing": Parallelization based on processes. Uses
                the "loky" backend in joblib to serialize the jobs and run them
                in separate processes. Using separate processes has a bigger
                memory and initialization overhead than threads, but may
                provide better scalability if perfomance is limited by the
                Global Interpreter Lock (GIL).

        Returns:
            np.ndarray | scipy.sparse.csr_matrix | list: The descriptor output
            for each given input. The return type depends on the desciptor
            setup.
        """
        # Check backend
        valid_backends = set(("loky", "threading"))
        if backend not in valid_backends:
            raise ValueError(
                "Invalid parallelization backend '{}' provided. Use one of the "
                "following: {}".format(backend, ", ".join(valid_backends))
            )

        # Split data into n_jobs (almost) equal jobs
        n_samples = len(inp)
        n_features = self.get_number_of_features()
        is_sparse = self._sparse
        k, m = divmod(n_samples, n_jobs)
        jobs = (inp[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n_jobs))

        # Calculate the result in parallel with joblib
        if output_sizes is None:
            output_sizes = n_jobs*[None]

        def create_multiple(arguments, func, is_sparse, n_features, n_desc, index, verbose):
            """This is the function that is called by each job but with
            different parts of the data.
            """
            # Initialize output
            if is_sparse:
                data = []
                rows = []
                cols = []
            else:
                if n_desc is not None:
                    results = np.empty((n_desc, n_features), dtype=np.float32)
                else:
                    results = []

            offset = 0
            i_sample = 0
            old_percent = 0
            n_samples = len(arguments)

            for i_sample, i_arg in enumerate(arguments):
                i_out = func(*i_arg)

                if is_sparse:
                    data.append(i_out.data)
                    rows.append(i_out.row + offset)
                    cols.append(i_out.col)
                else:
                    if n_desc is None:
                        results.append(i_out)
                    else:
                        results[offset:offset+i_out.shape[0], :] = i_out
                        offset += i_out.shape[0]

                if verbose:
                    current_percent = (i_sample+1)/n_samples*100
                    if current_percent >= old_percent + 1:
                        old_percent = current_percent
                        print("Process {0}: {1:.1f} %".format(index, current_percent))

            if is_sparse:
                data = np.concatenate(data)
                rows = np.concatenate(rows)
                cols = np.concatenate(cols)
                results = coo_matrix((data, (rows, cols)), shape=[n_desc, n_features], dtype=np.float32)

            return (results, index)

        with parallel_backend(backend, n_jobs=n_jobs):
            vec_lists = Parallel()(delayed(create_multiple)(i_args, func, is_sparse, n_features, n_desc, index, verbose) for index, (i_args, n_desc) in enumerate(zip(jobs, output_sizes)))

        # When using the threading backend the order has to be explicitly
        # restored
        if backend == "threading":
            vec_lists.sort(key=lambda x: x[1])

        # Remove the job index
        vec_lists = [x[0] for x in vec_lists]

        if self._sparse:
            row_offset = 0
            data = []
            cols = []
            rows = []
            n_descs = 0
            for i, i_res in enumerate(vec_lists):
                n_descs += i_res.shape[0]
                i_res = i_res.tocoo()
                i_n_desc = i_res.shape[0]
                i_data = i_res.data
                i_col = i_res.col
                i_row = i_res.row

                data.append(i_data)
                rows.append(i_row + row_offset)
                cols.append(i_col)

                # Increase the row offset
                row_offset += i_n_desc

            # Saves the descriptors as a sparse matrix
            data = np.concatenate(data)
            rows = np.concatenate(rows)
            cols = np.concatenate(cols)
            results = coo_matrix((data, (rows, cols)), shape=[n_descs, n_features], dtype=np.float32)

            # The final output is transformed into CSR form which is faster for
            # linear algebra
            results = results.tocsr()
        else:
            if n_features is None:
                results = []
                for part in vec_lists:
                    results.extend(part)
            else:
                results = np.concatenate(vec_lists, axis=0)

        return results
