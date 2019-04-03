# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals
from builtins import (bytes, str, open, super, range, zip, round, input, int, pow, object)

import numpy as np

from ase import Atoms

from dscribe.core import System
from dscribe.descriptors.matrixdescriptor import MatrixDescriptor


class CoulombMatrix(MatrixDescriptor):
    """Calculates the zero padded Coulomb matrix.

    The Coulomb matrix is defined as:

        C_ij = 0.5 Zi**exponent      | i = j
             = (Zi*Zj)/(Ri-Rj)	     | i != j

    The matrix is padded with invisible atoms, which means that the matrix is
    padded with zeros until the maximum allowed size defined by n_max_atoms is
    reached.

    To reach invariance against permutation of atoms, specify a valid option
    for the permutation parameter.

    For reference, see:
        "Fast and Accurate Modeling of Molecular Atomization Energies with
        Machine Learning", Matthias Rupp, Alexandre Tkatchenko, Klaus-Robert
        Mueller, and O.  Anatole von Lilienfeld, Phys. Rev. Lett, (2012),
        https://doi.org/10.1103/PhysRevLett.108.058301
    and
        "Learning Invariant Representations of Molecules for Atomization Energy
        Prediction", Gregoire Montavon et. al, Advances in Neural Information
        Processing Systems 25 (NIPS 2012)
    """
    def create(self, system, n_jobs=1, verbose=False, backend="threading"):
        """Return the Coulomb matrix for the given systems.

        Args:
            system (single or multiple class:`ase.Atoms`): One or many atomic structures.
            n_jobs (int): Number of parallel jobs to instantiate. Parallellizes
                the calculation across samples. Defaults to serial calculation
                with n_jobs=1.
            verbose(bool): Controls whether to print the progress of each job
                into to the console.
            backend (str): The parallelization method. Valid options are:

                * "threading": Parallelization based on threads. Has bery low
                memory and initialization overhead. Performance is limited by
                the amount of pure python code that needs to run. Ideal when
                most of the calculation time is used by C/C++ extensions that
                release the Global Interpreter Lock (GIL).
                * "multiprocessing": Parallelization based on processes. Uses
                the "loky" backend in joblib to serialize the jobs and run them
                in separate processes. Using separate processes has a bigger
                memory and initialization overhead than threads, but may
                provide better scalability if perfomance is limited by the
                Global Interpreter Lock (GIL).

        Returns:
            np.ndarray | scipy.sparse.csr_matrix: The Coulomb matrix output for
            the given systems. The return type depends on the
            'sparse'-attribute. The first dimension is determined by the amount
            of positions and systems and the second dimension is determined by
            the get_number_of_features()-function.
        """
        # If single system given, skip the parallelization
        if isinstance(system, (Atoms, System)):
            return self.create_single(system)

        # Combine input arguments
        inp = [(i_sys,) for i_sys in system]

        # Here we precalculate the size for each job to preallocate memory.
        n_samples = len(system)
        k, m = divmod(n_samples, n_jobs)
        jobs = (inp[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n_jobs))
        output_sizes = [len(job) for job in jobs]

        # Create in parallel
        output = self.create_parallel(inp, self.create_single, n_jobs, output_sizes, verbose=verbose, backend=backend)

        return output

    def get_matrix(self, system):
        """Creates the Coulomb matrix for the given system.

        Args:
            system (:class:`ase.Atoms` | :class:`.System`): Input system.

        Returns:
            np.ndarray: Coulomb matrix as a 2D array.
        """
        # Make sure that the system is non-periodic
        system.set_pbc(False)

        # Calculate offdiagonals
        q = system.get_atomic_numbers()
        qiqj = q[None, :]*q[:, None]
        idmat = system.get_inverse_distance_matrix()
        np.fill_diagonal(idmat, 0)
        cmat = qiqj*idmat

        # Set diagonal
        np.fill_diagonal(cmat, 0.5 * q ** 2.4)

        return cmat
