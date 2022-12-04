"""compute an Alternating Projection (AP)."""

# Authors: Yuval Realpe <yuval.realpe@gmail.com>
#
# License: BSD-3-Clause

from copy import copy

import numpy as np
from numpy.linalg import pinv, multi_dot, lstsq

from ..utils import _check_info_inv, verbose, fill_doc
from ._compute_beamformer import _prepare_beamformer_input
from ..io.pick import pick_channels_forward, pick_channels_evoked, pick_info
from ..forward.forward import convert_forward_solution, is_fixed_orient
from ..inverse_sparse.mxne_inverse import _make_dipoles_sparse
from ..minimum_norm.inverse import _log_exp_var


def matmul_transpose(mat):
    """Dot product of array with its transpose."""
    return np.matmul(mat, mat.transpose())


def _produce_data_cov(data_arr, attr_dict):
    """Calculate data covarience."""
    nsources = attr_dict['nsources']
    data_cov = matmul_transpose(data_arr) + matmul_transpose(data_arr).trace()\
        * np.eye(data_arr.shape[0])  # Array Covarience Matrix
    print(' alternating projection ; nsources = {}:'.format(nsources))

    return data_cov


def _fixed_phase1a(attr_dict, data_cov, gain):
    """Calculate phase 1a of fixed oriented AP.

    Initialization: search the 1st source location over the entire
    dipoles topographies space.

    Parameters
    ----------
    attr_dict : dict
        See: _calculate_fixed_alternating_projections.
    data_cov : array
        Data covarience.
    gain : array, shape (nchannels, ndipoles)
        Gain matrix.

    Returns
    -------
    s_ap : list of int
        List of dipole indices.

    """
    s_ap = []
    ap_val1 = np.zeros(attr_dict['ndipoles'])
    for dip in range(attr_dict['ndipoles']):
        l_p = np.expand_dims(gain[:, dip], axis=1)
        ap_val1[dip] = multi_dot([l_p.transpose(), data_cov, l_p]) \
            / (matmul_transpose(l_p.transpose())[0, 0])
    s1_idx = np.argmax(ap_val1)
    s_ap.append(s1_idx)
    return s_ap


def _fixed_phase1b(gain, s_ap, data_cov, attr_dict):
    """Calculate phase 1b of fixed oriented AP.

    Adding one source at a time.

    Parameters
    ----------
    gain : array, shape (nchannels, ndipoles)
        Gain matrix.
    s_ap : list of int
        List of dipole indices.
    data_cov : array
        Data covarience.
    attr_dict : dict
        See: _calculate_fixed_alternating_projections.

    Returns
    -------
    s_ap : list of int
        List of dipole indices.

    """
    for _ in range(1, attr_dict['nsources']):
        ap_val2 = np.zeros(attr_dict['ndipoles'])
        sub_g = gain[:, s_ap]
        act_spc = multi_dot([sub_g, pinv(matmul_transpose(sub_g.transpose())),
                             sub_g.transpose()])
        perpend_spc = np.eye(act_spc.shape[0]) - act_spc
        for dip in range(attr_dict['ndipoles']):
            l_p = np.expand_dims(gain[:, dip], axis=1)
            ap_val2[dip] = multi_dot([l_p.transpose(),
                                      perpend_spc,
                                      data_cov,
                                      perpend_spc,
                                      l_p])\
                / ((multi_dot([l_p.transpose(),
                               perpend_spc,
                               l_p]))[0, 0])
        s2_idx = np.argmax(ap_val2)
        s_ap.append(s2_idx)
    print('current s_ap = {}'.format(s_ap))
    return s_ap


def _fixed_phase2(attr_dict, s_ap_2, gain, data_cov):
    """Calculate phase 2 of fixed oriented AP.

    Altering the projection of current estimated dipoles.

    Parameters
    ----------
    attr_dict : dict
        See: _calculate_fixed_alternating_projections.
    s_ap_2 : list of int
        List of dipole indices.
    gain : array, shape (nchannels, ndipoles)
        Gain matrix.
    data_cov : array
        Data covarience.

    Returns
    -------
    s_ap_2 : list of int
        List of dipole indices.

    """
    for itr in range(attr_dict['max_iter']):
        print('iteration No. {}'.format(itr + 1))
        s_ap_2_prev = copy(s_ap_2)
        for src in range(attr_dict['nsources']):
            # AP localization of src-th source
            ap_val2 = np.zeros(attr_dict['ndipoles'])
            s_ap_temp = copy(s_ap_2)
            s_ap_temp.pop(src)
            sub_g = gain[:, s_ap_temp]
            act_spc = multi_dot([sub_g,
                                 pinv(matmul_transpose(sub_g.transpose())),
                                 sub_g.transpose()])
            perpend_spc = np.eye(act_spc.shape[0]) - act_spc
            for dip in range(attr_dict['ndipoles']):
                l_p = np.expand_dims(gain[:, dip], axis=1)
                ap_val2[dip] = multi_dot([l_p.transpose(),
                                          perpend_spc,
                                          data_cov,
                                          perpend_spc,
                                          l_p])\
                    / ((multi_dot([l_p.transpose(),
                       perpend_spc, l_p]))[0, 0])
            s2_idx = np.argmax(ap_val2)
            s_ap_2[src] = s2_idx
        print('current s_ap_2 = {}'.format(s_ap_2))
        if (itr > 0) & (s_ap_2_prev == s_ap_2):
            # No improvement vs. previous iteration
            print('Done (optimally)')
            break
        if itr == attr_dict['max_iter']:
            print('Done (max iteration)')
    return s_ap_2


def _calculate_fixed_alternating_projections(data_arr, gain,
                                             nsources,
                                             max_iter):
    """Calculate fixed-orientation alternating projection.

    Parameters
    ----------
    data_arr : array, shape (nchannels, times)
        Filttered evoked data array.
    gain : array, shape (nchannels, ndipoles)
        Gain matrix.
    nsources : int
        The number of dipoles to estimate.
    max_iter : int
        Maximal iteration number of AP.

    Returns
    -------
    s_ap_2 : list of int
        List of dipole indices.

    """
    s_ap = []
    ndipoles = gain.shape[1]
    attr_dict = {
        'ndipoles': ndipoles,
        'nsources': nsources,
        'max_iter': max_iter
    }
    print('calculating fixed-orientation alternating projection')
    data_cov = _produce_data_cov(data_arr, attr_dict)

    # ######################################
    # 1st Phase
    # (a) Initialization: search the 1st source location over the entire
    # dipoles topographies space
    # ######################################

    print(' 1st phase : ')
    s_ap = _fixed_phase1a(attr_dict, data_cov, gain)

    # ######################################
    # (b) Now, add one source at a time
    # ######################################

    s_ap = _fixed_phase1b(gain, s_ap, data_cov, attr_dict)

    # #####################################
    # 2nd phase
    # #####################################

    print(' 2nd phase : ')
    s_ap_2 = copy(s_ap)
    s_ap_2 = _fixed_phase2(attr_dict, s_ap_2, gain, data_cov)

    return s_ap_2


def _solve_active_gain_eig(ind, gain, data_cov, eig, perpend_spc):
    """Eigen values and vector of the projection."""
    gain_idx = list(range(ind * 3, ind * 3 + 3))
    l_p = gain[:, gain_idx]
    eig_a = multi_dot([l_p.transpose(),
                       perpend_spc,
                       data_cov,
                       perpend_spc,
                       l_p])
    eig_b = multi_dot([l_p.transpose(),
                       perpend_spc,
                       perpend_spc,
                       l_p])
    eig_b = eig_b + 1e-3 * eig_b.trace() * np.eye(3)
    eig_val, eig_vec = eig(eig_a, eig_b)

    return eig_val, eig_vec, l_p


def _free_phase1a(attr_dict, gain, data_cov):
    """Calculate phase 1a of free oriented AP.

    Initialization: search the 1st source location over
    the entire dipoles topographies space.

    Parameters
    ----------
    attr_dict : dict
        See: _calculate_free_alternating_projections.
    data_cov : array
        Data covarience.
    gain : array, shape (nchannels, ndipoles)
        Gain matrix.

    Returns
    -------
    s_ap : list of int
        List of dipole indices.
    oris : array, shape (nsources, 3)
        Orientations array of estimated sources (sorted by s_ap).
    sub_g_proj : array
        Sub space projected by estimated dipoles.

    """
    from scipy.linalg import eig

    s_ap = []
    oris = np.empty((attr_dict['nsources'], 3))
    ap_val1 = np.zeros(attr_dict['ndipoles'])
    perpend_spc = np.eye(gain.shape[0])
    for dip in range(attr_dict['ndipoles']):
        sol_tuple = _solve_active_gain_eig(dip, gain,
                                           data_cov, eig,
                                           perpend_spc)
        ap_val1[dip] = np.max([x.real for x in sol_tuple[0]])

    # obtain the 1st source location
    s1_idx = np.argmax(ap_val1)
    s_ap.append(s1_idx)

    # obtain the 1st source orientation
    sol_tuple = _solve_active_gain_eig(s1_idx, gain,
                                       data_cov, eig,
                                       perpend_spc)
    oris[0] = sol_tuple[1][:,
                           [np.argmax([x.real for x
                                      in sol_tuple[0]])]
                           ][:, 0]
    sub_g_proj = np.dot(sol_tuple[2], oris[0])[:, np.newaxis]
    return s_ap, oris, sub_g_proj


def _free_phase1b(attr_dict, gain, data_cov, ap_temp_tuple):
    """Calculate phase 1b of free oriented AP.

    Adding one source at a time.

    Parameters
    ----------
    ap_temp_tuple : tuple
        See: _free_phase1a.
    attr_dict : dict
        See: _calculate_free_alternating_projections.
    data_cov : array
        Data covarience.
    gain : array, shape (nchannels, ndipoles)
        Gain matrix.

    Returns
    -------
    s_ap : list of int
        List of dipole indices.
    oris : array, shape (nsources, 3)
        Orientations array of estimated sources (sorted by s_ap).
    sub_g_proj : array
        Sub space projected by estimated dipoles.

    """
    # ap_temp_tuple = (s_ap, oris, sub_g_proj)
    from scipy.linalg import eig

    s_ap, oris, sub_g_proj = copy(ap_temp_tuple)
    for src in range(1, attr_dict['nsources']):
        ap_val2 = np.zeros(attr_dict['ndipoles'])
        act_spc = multi_dot([sub_g_proj,
                            pinv(matmul_transpose(sub_g_proj.transpose())),
                            sub_g_proj.transpose()])
        perpend_spc = np.eye(act_spc.shape[0]) - act_spc
        for dip in range(attr_dict['ndipoles']):
            sol_tuple = _solve_active_gain_eig(dip, gain,
                                               data_cov, eig,
                                               perpend_spc)
            ap_val2[dip] = np.max([x.real for x in sol_tuple[0]])

        s2_idx = np.argmax(ap_val2)
        s_ap.append(s2_idx)
        sol_tuple = _solve_active_gain_eig(s2_idx, gain,
                                           data_cov, eig,
                                           perpend_spc)
        oris[src] = sol_tuple[1][:,
                                 [np.argmax([x.real for x
                                            in sol_tuple[0]])]
                                 ][:, 0]
        sub_g_proj = np.concatenate([sub_g_proj,
                                     np.dot(sol_tuple[2],
                                            oris[src])[:, np.newaxis]
                                     ],
                                    axis=1)
    return s_ap, oris, sub_g_proj


def _free_phase2(ap_temp_tuple, attr_dict, data_cov, gain):
    """Calculate phase 2 of free oriented AP.

    altering the projection of current estimated dipoles

    Parameters
    ----------
    ap_temp_tuple : tuple
        See: _free_phase1b.
    attr_dict : dict
        See: _calculate_free_alternating_projections.
    data_cov : array
        Data covarience.
    gain : array, shape (nchannels, ndipoles)
        Gain matrix.

    Returns
    -------
    s_ap_2 : list of int
        List of dipole indices.
    oris : array, shape (nsources, 3)
        Orientations array of estimated sources (sorted by s_ap_2).
    sub_g_proj : array
        Sub space projected by estimated dipoles.

    """
    # ap_temp_tuple = (s_ap, oris, sub_g_proj)
    from scipy.linalg import eig

    s_ap_2, oris, sub_g_proj = copy(ap_temp_tuple)
    print(' 2nd phase : ')
    for itr in range(attr_dict['max_iter']):
        print('iteration No. {}'.format(itr + 1))
        s_ap_2_prev = copy(s_ap_2)
        for src in range(attr_dict['nsources']):
            # AP localization of src-th source
            ap_val2 = np.zeros(attr_dict['ndipoles'])
            a_tmp = copy(ap_temp_tuple[2])
            a_tmp = np.delete(a_tmp, src, 1)
            act_spc = multi_dot([a_tmp,
                                 pinv(matmul_transpose(a_tmp.transpose())),
                                 a_tmp.transpose()])
            perpend_spc = np.eye(act_spc.shape[0]) - act_spc
            for dip in range(attr_dict['ndipoles']):
                sol_tuple = _solve_active_gain_eig(dip, gain, data_cov,
                                                   eig, perpend_spc)
                ap_val2[dip] = np.max([x.real for x in sol_tuple[0]])

            sq_idx = np.argmax(ap_val2)
            s_ap_2[src] = sq_idx
            sol_tuple = _solve_active_gain_eig(sq_idx, gain, data_cov,
                                               eig, perpend_spc)
            oris[src] = sol_tuple[1][:, [np.argmax([x.real for x
                                                    in sol_tuple[0]])]][:, 0]
            sub_g_proj[:, src] = np.dot(sol_tuple[2], oris[src])

        print('current s_ap_2 = {}'.format(s_ap_2))
        if (itr > 0) & (s_ap_2_prev == s_ap_2):
            # No improvement vs. previous iteration
            print('Done (optimally)')
            break
        if itr == attr_dict['max_iter']:
            print('Done (max iteration)')

    return s_ap_2, oris, sub_g_proj


def _calculate_free_alternating_projections(data_arr, gain,
                                            nsources, max_iter):
    """Calculate free-orientation alternating projection.

    Parameters
    ----------
    data_arr : array, shape (nchannels, times)
        Filttered evoked data array.
    gain : array, shape (nchannels, ndipoles)
        Gain array.
    nsources : int
        The number of dipoles to estimate.
    max_iter : int
        Maximal iteration number of AP.

    Returns
    -------
    ap_temp_tuple : tuple
        See: _free_phase2.

    """
    print('calculating free-orientation alternating projection')
    ndipoles = int(gain.shape[1] / 3)
    attr_dict = {
        'ndipoles': ndipoles,
        'nsources': nsources,
        'max_iter': max_iter
    }

    data_cov = _produce_data_cov(data_arr, attr_dict)
    # ######################################
    # 1st Phase
    # (a) Initialization: search the 1st source location over the entire
    # dipoles topographies space
    # ######################################

    print(' 1st phase : ')
    ap_temp_tuple = _free_phase1a(attr_dict, gain, data_cov)
    # ap_temp_tuple = (s_ap, oris, sub_g_proj)

    # ######################################
    # (b) Now, add one source at a time
    # ######################################

    ap_temp_tuple = _free_phase1b(attr_dict, gain, data_cov, ap_temp_tuple)
    # ap_temp_tuple = (s_ap, oris, sub_g_proj)
    print('current s_ap = {}'.format(ap_temp_tuple[0]))

    # #####################################
    # 2nd phase
    # #####################################

    ap_temp_tuple = _free_phase2(ap_temp_tuple, attr_dict, data_cov, gain)

    return ap_temp_tuple


def free_ori_ap(wh_data, gain, nsources, forward, max_iter):
    """Branch of calculations dedicated to freely oriented dipoles."""
    sol_tuple = \
        _calculate_free_alternating_projections(wh_data, gain,
                                                nsources, max_iter)
    # sol_tuple = active_idx, active_orientations, active_idx_gain

    sol = lstsq(sol_tuple[2], wh_data, rcond=None)[0]

    gain_fwd = forward['sol']['data'].copy()
    gain_fwd.shape = (gain_fwd.shape[0], -1, 3)
    gain_active = gain_fwd[:, sol_tuple[0]]
    gain_dip = (sol_tuple[1] * gain_active).sum(-1)
    idx = np.array(sol_tuple[0])
    active_set = np.array(
        [[3 * idx, 3 * idx + 1, 3 * idx + 2]]
    ).T.ravel()

    return (
        active_set,
        sol_tuple[1],
        forward['source_rr'][sol_tuple[0]],
        gain_active, gain_dip, sol, sol_tuple[0]
    )


def fixed_ori_ap(wh_data, gain, nsources, forward, max_iter):
    """Branch of calculations dedicated to fixed oriented dipoles."""
    idx = _calculate_fixed_alternating_projections(wh_data, gain,
                                                   nsources=nsources,
                                                   max_iter=max_iter)

    sub_g = gain[:, idx]
    sol = lstsq(sub_g, wh_data, rcond=None)[0]

    gain_fwd = forward['sol']['data'].copy()
    gain_fwd.shape = (gain_fwd.shape[0], -1, 1)
    gain_active = gain_fwd[:, idx]
    gain_dip = gain_active[:, :, 0]

    return (
        idx,
        forward['source_nn'][idx],
        forward['source_rr'][idx],
        gain_active, gain_dip, sol
    )


@fill_doc
def _apply_ap(data, info, times, forward, noise_cov,
              nsources, picks, max_iter):
    """AP for evoked data.

    Parameters
    ----------
    data : array, shape (n_channels, n_times)
        Evoked data.
    %(info_not_none)s
    times : array
        Time sampling values.
    forward : instance of Forward
        Forward operator.
    noise_cov : instance of Covarience
        The noise covarience.
    nsources : int
        The number of dipoles to estimate.
    picks : List of int
        Channel indiecs for filtering.
    max_iter : int
        Maximal iteration number of AP.

    Returns
    -------
    dipoles : list of instances of Dipole
        The dipole fits.
    explained_data : array
        Data explained by the dipoles using a least square fitting with the
        selected active dipoles and their estimated orientation.
    var_exp : float
        Percentile of data variation explained (see: _log_exp_var).
    dip_ind : List of int
        List of indices of dipole source estimated.
    oris : array, shape (nsources, 3)
        Orientations array of estimated sources (sorted by dip_ind).
    poss : array, shape (nsources, 3)
        Coordinates array of estimated sources (sorted by dip_ind).

    """
    info = pick_info(info, picks)
    del picks

    if forward['surf_ori'] and not is_fixed_orient(forward):
        forward = convert_forward_solution(forward, surf_ori=False)
    is_free_ori, info, _, _, gain, whitener, _, _ = _prepare_beamformer_input(
        info, forward, noise_cov=noise_cov, rank=None)
    forward = pick_channels_forward(forward, info['ch_names'], ordered=True)
    del info

    # whiten the data (leadfield already whitened)
    wh_data = np.dot(whitener, data)
    del data

    if is_free_ori:
        idx, oris, poss, gain_active, gain_dip, sol, dip_ind = \
            free_ori_ap(wh_data, gain, nsources, forward,
                        max_iter=max_iter)
        X = sol[:, np.newaxis] * oris[:, :, np.newaxis]
        X.shape = (-1, len(times))
    else:
        idx, oris, poss, gain_active, gain_dip, sol = \
            fixed_ori_ap(wh_data, gain, nsources, forward,
                         max_iter=max_iter)
        X = sol
        dip_ind = idx

    gain_active = whitener @ gain_active.reshape(gain.shape[0], -1)
    explained_data = gain_dip @ sol
    m_estimate = whitener @ explained_data
    var_exp = _log_exp_var(wh_data, m_estimate)
    tstep = np.median(np.diff(times)) if len(times) > 1 else 1.
    dipoles = _make_dipoles_sparse(
        X, idx, forward, times[0], tstep, wh_data,
        gain_active, active_is_idx=True)
    for dipole, ori in zip(dipoles, oris):
        signs = np.sign((dipole.ori * ori).sum(-1, keepdims=True))
        dipole.ori *= signs
        dipole.amplitude *= signs[:, 0]

    return dipoles, explained_data, var_exp, dip_ind, oris, poss


def _residual_packing(evoked, picks, explained_data_mat, info):
    """Pack the residual data into an mne.Evoked object."""
    residual = evoked.copy()
    selection = [info['ch_names'][pick] for pick in picks]

    residual = pick_channels_evoked(residual,
                                    include=selection)
    residual.data -= explained_data_mat
    active_projs = [proj for proj in residual.info['projs'] if proj['active']]
    for proj in active_projs:
        proj['active'] = False
    residual.add_proj(active_projs, remove_existing=True)
    residual.apply_proj()
    return residual


def _explained_data_packing(evoked, picks, explained_data_mat, info):
    """Pack the explained data into an mne.Evoked object."""
    info = evoked.info
    explained_data = evoked.copy()
    selection = [info['ch_names'][pick] for pick in picks]

    explained_data = pick_channels_evoked(explained_data,
                                          include=selection)
    explained_data.data = explained_data_mat
    active_projs = [proj for proj in
                    explained_data.info['projs'] if proj['active']]
    for proj in active_projs:
        proj['active'] = False
    explained_data.add_proj(active_projs, remove_existing=True)
    explained_data.apply_proj()
    return explained_data


@verbose
def ap(evoked, forward, nsources, noise_cov=None, max_iter=6,
       return_residual=True, return_active_info=False, verbose=None):
    """AP sources localization method.

    Compute Alternating Projection (AP) on evoked data.

    Parameters
    ----------
    evoked : instance of Evoked
        Evoked object containing data to be localized.
    forward : instance of Forward
        Forward operator.
    nsources : int
        The number of dipoles to estimate.
    noise_cov : instance of Covarience, optional
        The noise covarience. The default is None.
    max_iter : int, optional
        Maximal iteration number of AP. The default is 6.
    return_residual : bool, optional
        If True, appends residual, explained_data and var_exp to output.
        The default is True.
    return_active_info : bool, optional
        If True, appends estimated source's information
        (indices,coordinates,orientation). The default is False.
    %(verbose)s

    Returns
    -------
    output : list
        Default:
            dipoles : list of instance of Dipole
                The dipole fits.
        If return_residual:
            residual : instance of Evoked
                Data not explained by the dipoles.
            explained_data : instance of Evoked
                Data explained by the dipoles.
            var_exp : float
                Percentile of data variation explained (see: _log_exp_var).
        If return_active_info :
            idx : list of int
                List of indices of dipole source estimated.
            poss : array, shape (nsources, 3)
                Coordinates array of estimated sources (sorted by idx).
            oris : array, shape (nsources, 3)
                Orientations array of estimated sources (sorted by idx).

    Notes
    -----
    The references are:

        A. Amir, M. Wax and P. Dimitrios. 2022. Brain Source Localization by
        Alternating Projection. IEEE International Symposium on Biomedical
        Imaging (ISBI). doi: 10.48550/ARXIV.2202.01120
        https://doi.org/10.48550/arxiv.2202.01120

        A. Amir, M. Wax and P. Dimitrios. 2020. Localization of MEG and EEG
        Brain Signals by Alternating Projection. doi: 10.48550/ARXIV.1908.11416
        https://doi.org/10.48550/arxiv.1908.11416

    .. versionadded:: 1.3.dev0
    """
    info = evoked.info
    data = evoked.data
    times = evoked.times

    picks = _check_info_inv(info, forward, data_cov=None,
                            noise_cov=noise_cov)

    data = data[picks]

    dipoles, explained_data_mat, var_exp, idx, oris, poss = \
        _apply_ap(data, info, times, forward, noise_cov,
                  nsources, picks, max_iter=max_iter)

    output = [dipoles]
    if return_residual:

        # treating residual
        residual = _residual_packing(evoked, picks,
                                     explained_data_mat, info)

        # treating explained data
        info = evoked.info
        explained_data = _explained_data_packing(evoked, picks,
                                                 explained_data_mat, info)

        for item in [residual, explained_data, var_exp]:
            output.append(item)

    if return_active_info:
        for item in [idx, poss, oris]:
            output.append(item)
    elif not return_residual:
        output = output[0]

    return output