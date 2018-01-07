# -*- Mode: python; tab-width: 4; indent-tabs-mode:nil; coding: utf-8 -*-
# vim: tabstop=4 expandtab shiftwidth=4 softtabstop=4
""" Module: correlator
    ==================
"""
from __future__ import print_function

import numpy as np
from pytim import utilities
from MDAnalysis.core.groups import Atom, AtomGroup, Residue, ResidueGroup


class Correlator(object):
    """ Computes the (self) correlation of an observable (scalar or vector)

    :param Observable observable: compute the autocorrelation of this observable. If observable is None and reference is \
                                  not, the survival probability in the  
    :param bool normalize: normalize the correlation to 1 at t=0
    :param AtomGroup reference: if the group passed to the sample() function changes its composition along the trajectory \
                                (such as a layer group), a reference group that includes all atoms that could appear in the \
                                variable group must be passed, in order to provide a proper normalization. See\ the example \
                                below.
    :param double memory_warn: if not None, print a warning once this threshold of memory (in Mb) is passed.


    Example:

    >>> import pytim
    >>> import MDAnalysis as mda
    >>> import numpy as np
    >>> from pytim.datafiles import WATERSMALL_GRO
    >>> from pytim.utilities import lap
    >>> #  tmpdir here is specified only for travis
    >>> WATERSMALL_TRR = pytim.datafiles.pytim_data.fetch('WATERSMALL_LONG_TRR',tmpdir='./') # doctest:+ELLIPSIS
    checking presence of a cached copy ...

    >>> u = mda.Universe(WATERSMALL_GRO,WATERSMALL_TRR)
    >>> g = u.select_atoms('name OW')

    >>> velocity = pytim.observables.Velocity()
    >>> corr = pytim.observables.Correlator(observable=velocity)
    >>> for t in u.trajectory[1:]:
    ...     corr.sample(g)
    >>> vacf = corr.correlation()

    This produces the following (steps of 1 fs):

    .. plot::

        import pytim
        import MDAnalysis as mda
        import numpy as np
        from pytim.datafiles import WATERSMALL_GRO
        from pytim.utilities import lap
        WATERSMALL_TRR=pytim.datafiles.pytim_data.fetch('WATERSMALL_LONG_TRR')

        u = mda.Universe(WATERSMALL_GRO,WATERSMALL_TRR)
        g = u.select_atoms('name OW')

        velocity = pytim.observables.Velocity()
        corr = pytim.observables.Correlator(observable=velocity)
        for t in u.trajectory[1:]:
            corr.sample(g)

        vacf = corr.correlation()

        from matplotlib import pyplot as plt
        plt.plot(vacf[:1000])
        plt.plot([0]*1000)
        plt.show()


    In order to compute the correlation for variable groups, one should proceed
    as follows:

    >>> corr = pytim.observables.Correlator(observable=velocity,reference=g)
    >>> # notice the molecular=False switch, in order for the
    >>> # layer group to be made of oxygen atoms only and match
    >>> # the reference group
    >>> inter = pytim.ITIM(u,group=g,alpha=2.0,molecular=False)
    >>> # example only: sample longer for smooth results
    >>> for t in u.trajectory[1:10]: 
    ...     corr.sample(inter.atoms)
    >>> layer_vacf = corr.correlation()


    In order to compute the survival probability of some atoms in a layer, it is possible
    to pass observable=None together with the reference group:

    >>> corr = pytim.observables.Correlator(observable=None, reference = g)
    >>> inter = pytim.ITIM(u,group=g,alpha=2.0, molecular=False)
    >>>  # example only: sample longer for smooth results
    >>> for t in u.trajectory[1:10]:
    ...     corr.sample(inter.atoms)
    >>> survival = corr.correlation()

    """

    def __init__(self,
                 universe=None,
                 observable=None,
                 reference=None,
                 memory_warn=None):
        self.name = self.__class__.__name__
        self.observable = observable
        self.reference = reference
        self.timeseries = []
        self.maskseries = []
        self.shape = None
        self.mem_usage = 0.0
        self.warned = False
        self.nsamples = 0

        if self.reference is not None and self.observable is not None:
            self.masked = True
        else:
            self.masked = False

        if memory_warn is None:
            self.warned = True
            self.memory_warn = 0.0
        else:
            self.memory_warn = memory_warn
        if reference is not None:
            if observable is not None:
                self.reference_obs = observable.compute(reference) * 0.0
            else:
                self.reference_obs = np.zeros(len(reference), dtype=np.double)
            if len(self.reference_obs.shape) > 2:
                raise RuntimeError(
                    self.name + ' works only with scalar and vectors')
        else:
            if observable is None:
                raise RuntimeError(
                    self.name +
                    ': at least the observable or the reference must be specified'
                )

    def sample(self, group):
        """ Sample the timeseries for the autocorrelation function

            :parameter AtomGroup group: compute the observable on the atoms of this group

        """
        self.nsamples += 1
        if self.reference is not None:  # could be intermittent or continuous:
            # we need to collect also the residence
            # function
            # the residence function (1 if in the reference group, 0 otherwise)
            mask = np.isin(self.reference, group)
            # append the residence function to its timeseries
            self.maskseries.append(list(mask))
            if self.observable is not None:
                # this copies a vector of zeros with the correct shape
                sampled = self.reference_obs.copy()
                obs = self.observable.compute(group)
                sampled[np.where(mask)] = obs
                self.timeseries.append(list(sampled.flatten()))
            else:
                self.timeseries = self.maskseries
                if self.shape is None:
                    self.shape = (1, )
                sampled = mask
        else:
            if self.observable is None:
                RuntimeError(
                    'Cannot compute the survival probability without a reference group'
                )
            sampled = self.observable.compute(group)
            self.timeseries.append(list(sampled.flatten()))

        self.mem_usage += sampled.nbytes / 1024.0 / 1024.0  # in Mb
        if self.mem_usage > self.memory_warn and self.warned == False:
            print("Warning: warning threshold of", end='')
            print(self.memory_warn + " Mb exceeded")
            self.warned = True

        if self.shape is None:
            self.shape = sampled.shape

    def correlation(self, normalized=True, continuous=True):
        """ Calculate the autocorrelation from the sampled data

            :parameter bool normalized: normalize the correlation function to: its zero-time value for
                                        regular correlations; to the average of the characteristic function
                                        for the survival probability.
            :parameter bool continuous: applies only when a reference group has been specified: if True 
                                        (default) the contribution of a particle at time lag $\\tau=t_1-t_0$
                                        is considered only if the particle did not leave the reference group
                                        between $t_0$ and $t_1$. If False, the intermittent correlation is
                                        calculated, and the above restriction is released.

            Example:

            >>> # We build a fake trajectory to test the various options:
            >>> import MDAnalysis as mda
            >>> import pytim
            >>> import numpy as np
            >>> from pytim.datafiles import WATER_GRO
            >>> from pytim.observables import Correlator, Velocity
            >>> np.set_printoptions(suppress=True,precision=3)
            >>> 
            >>> u = mda.Universe(WATER_GRO)
            >>> g = u.atoms[0:2]
            >>> g.velocities*=0.0
            >>> g.velocities+=1.0
            >>>
            >>> vv = Correlator(observable=Velocity('x'), reference=g) # velocity autocorrelation along x, variable group
            >>> nn = Correlator(reference=g) # survival probability in group g
            >>>
            >>> for c in [vv,nn]:
            ...     c.sample(g)     # t=0
            ...     c.sample(g)     # t=1
            ...     c.sample(g[:1]) # t=2, exclude the second particle
            ...     g.velocities /= 2. # from now on v=0.5
            ...     c.sample(g)     # t=3
            >>>

            The timeseries sampled can be accessed using:

            >>> print(vv.timeseries) # rows refer to time, columns to particle 
            [[1.0, 1.0], [1.0, 1.0], [1.0, 0.0], [0.5, 0.5]]
            >>>
            >>> print(nn.timeseries)
            [[True, True], [True, True], [True, False], [True, True]]
            >>>

            Note that the average of  the characteristic function $h(t)$ is done over all
            trajectories, including those that start with h=0. The correlation
            $< h(t) h(0) >$ is divided by the average $<h>$ computed over all trajectores that
            extend up to a time lag $t$. The `normalize` switch has no effect.

            >>> # normalized, continuous
            >>> corr = nn.correlation()
            >>> print (np.allclose(corr, [ 7./7, 4./5, 2./4, 1./2]))
            True
            >>> # normalized, intermittent 
            >>> corr = nn.correlation(continuous=False)
            >>> print (np.allclose(corr, [ 7./7, 4./5, 3./4, 2./2 ]))
            True

            The autocorrelation functions are calculated by taking into account in the average
            only those trajectory that start with $h=1$ (i.e., which start within the reference
            group). The normalization is done by dividing the correlation at time lag $t$ by its 
            value at time lag 0 computed over all trajectories that extend up to time lag $t$ and 
            do not start with $h=0$.

            >>> # not normalizd, intermittent
            >>> corr = vv.correlation(normalized=False,continuous=False)
            >>> print (np.allclose(corr, [ (1+1+1+0.25+1+1+0.25)/7, (1+1+0.5+1)/5, (1+0.5+0.5)/4, (0.5+0.5)/2]))
            True
            >>> # check normalization
            >>> np.all(vv.correlation(continuous=False) == corr/corr[0])
            True
            >>> # not normalizd, continuous
            >>> corr = vv.correlation(normalized=False,continuous=True)
            >>> print (np.allclose(corr, [ (1+1+1+0.25+1+1+0.25)/7, (1+1+0.5+1)/5 , (1+0.5)/4, (0.5+0.)/2]))
            True
            >>> # check normalization
            >>> np.all(vv.correlation(continuous=True) == corr/corr[0])
            True

        """
        intermittent = not continuous
        self.dim = self._determine_dimension()

        # the standard correlation
        if self.reference is None:
            ts = np.asarray(self.timeseries)
            corr = utilities.correlate(ts)
            norm = corr[:, 0]
            corr = np.average(corr, axis=1)
            if normalized == True:
                corr /= corr[0]
            return corr

        # prepare the mask for the intermittent/continuous cases
        if intermittent == True:
            ms = np.asarray(self.maskseries, dtype=np.double)
        else:  # we add Falses at the begining and at the end to ease the splitting in sub-trajectories
            falses = [[False] * len(self.maskseries[0])]
            ms = np.asarray(falses + self.maskseries + falses)

        # compute the survival probabily
        if self.observable is None:
            return self._survival_probability(ms, normalized, intermittent)

        # compute the autocorrelation function
        else:
            ts = np.asarray(self.timeseries)
            return self._autocorrelation(ts, ms, normalized, intermittent)

    def _autocorrelation(self, ts, ms, normalized, intermittent):

        if intermittent == True:
            corr = self._autocorrelation_intermittent(ts, ms)
        else:
            corr = self._autocorrelation_continuous(ts, ms)

        if normalized == True:
            corr = corr / corr[0]

        return corr

    def _survival_probability(self, ms, normalized, intermittent):
        if intermittent == True:
            corr = self._survival_intermittent(ms)
        else:
            corr = self._survival_continuous(ms)

        return corr

    def _survival_intermittent(self, ms):
        norm = None
        corr = np.sum(utilities.correlate(ms, _normalize=False), axis=1)
        return corr / np.sum(np.cumsum(self.timeseries, axis=0), axis=1)[::-1]

    def _survival_continuous(self, ms):
        norm = None
        n_part = len(ms[0])
        corr = np.zeros((self.nseries, n_part))
        norm = corr.copy()
        counting = (1. + np.arange(len(self.timeseries)))

        for part in range(n_part):
            edges = np.where(ms[::, part][:-1] != ms[::, part][1:])[0]
            deltat = edges[1::2] - edges[0::2]
            # for each of the disconnected segments:
            for n, dt in enumerate(deltat):
                # no need to compute the correlation, we know what it is
                corr[0:dt, part] += counting[:dt][::-1]

        corr = np.sum(corr, axis=1)
        return corr / np.sum(np.cumsum(self.timeseries, axis=0), axis=1)[::-1]

    def _autocorrelation_intermittent(self, ts, ms):

        dim = self.dim

        maskcorr = utilities.correlate(ms)
        corr = ts.copy()
        for xyz in range(dim):
            corr[:, xyz::dim] = utilities.correlate(
                ts[:, xyz::dim] * ms, _normalize=False)
        corr = np.sum(
            corr, axis=1) / np.sum(
                np.cumsum(ms, axis=0), axis=1)[::-1]

        return corr

    def _autocorrelation_continuous(self, ts, ms):

        dim = self.dim
        norm = None
        n_part = len(ms[0])
        corr = np.zeros((ts.shape[0], ts.shape[1] / dim))
        norm = np.zeros((ts.shape[0], ts.shape[1] / dim))

        for part in range(n_part):
            edges = np.where(ms[::, part][:-1] != ms[::, part][1:])[0]
            deltat = edges[1::2] - edges[0::2]
            for n, dt in enumerate(
                    deltat):  # for each of the disconnected segments
                t1, t2 = edges[2 * n], edges[2 * n + 1]
                i1, i2 = dim * part, dim * (part + 1)
                corr[0:dt, part] += np.sum(
                    utilities.correlate(ts[t1:t2, i1:i2], _normalize=False),
                    axis=1)

        return np.sum(
            corr, axis=1) / np.sum(
                np.cumsum(self.maskseries, axis=0)[::-1], axis=1)

    def _determine_dimension(self):
        self.nseries = max(len(self.timeseries), len(self.maskseries))

        if len(self.shape) == 1:
            shape = (self.nseries, self.shape[0], 1)
            dim = 1
        elif len(self.shape) == 2:
            shape = (self.nseries, self.shape[0], self.shape[1])
            dim = self.shape[1]
        else:
            raise RuntimeError(
                "Correlations of tensorial quantites not allowed in " +
                self.name)
        return dim
