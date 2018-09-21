#!/usr/bin/python
# ! encoding: UTF8
from __future__ import absolute_import

import numpy as np
from numpy import sqrt
from numpy.ma import sin, cos
from .coord.ecef import ecef_to_lat_lon_alt, sat_elev
from .parse_rinex import parse_rinex
# from .visualization.ellipsoid import satellites
# from .visualization.map import on_map
from .delays import tropmodel

__author__ = 'kirienko'

"""
This module provides least squares method of solution of navigational problem.

    TODO: weighted LS

1. Parse Nav file --> ephemerides --> sat coords (ECEF)
2. Parse Obs file --> pseudoranges to rover
3. Calculate rover's coords in ECEF
"""


# Pretty printing
def lla_string(R):
    return "φ = %.3f°, θ = %.3f°, h = %d m" % (R[0], R[1], int(R[2]))


def xyz_string(R):
    return "(%d, %d, %d) [km]" % tuple(map(int, R / 1000))


def nav_nearest_in_time(t, nav_array):
    """
    From array of NavGPS objects returns the one
        which ephemeris are the closets in time to ``t``
    :param t: UTC time
    :param nav_array:
    :return:
    """
    diff_array = [abs(n.t_oe - t) for n in nav_array]
    return nav_array[diff_array.index(min(diff_array))]


def distance(R1, R2):
    """
    Calculates euclidean distance (along the straight line)
    :param R1: vector in ECEF
    :param R2: vector in ECEF
    :return: Euclidean distance between R1 and R2 [in meters]
    """
    return sqrt(sum(map(lambda x, y: (x - y) ** 2, R1, R2)))


def least_squares(obs, navs, init_pos='', vmf_coeffs=()):
    """
    x = (A^TA)^{-1}A^T l
    Takes an observation ``obs`` and all the data ``nav`` from navigation file.
    If we have a-priori information about rover's position,
        then we can filter low satellites and use troposperic correction
    :return: rover's position in ecef [m]
    """
    c = 299792428  # speed of light
    elev_mask = 8  # satellite elevation mask
    now = obs.date
    # Find all possible satellites N
    sats = []
    for r in obs.PRN_number:
        # print r, "Data:", obs.obs_data['C1'][obs.prn(r)], obs.obs_data['P2'][obs.prn(r)]
        if obs.obs_data['C1'][obs.prn(r)] and obs.obs_data['P2'][obs.prn(r)] and ('G' in r):      # iono-free
        # if obs.obs_data['C1'][obs.prn(r)] and obs.obs_data['P1'][obs.prn(r)] and ('R' in r):      # iono-free
        # if obs.obs_data['C1'][i] and ('G' in r):                                # C1 only
            nnt = nav_nearest_in_time(now, navs[r])
            if len(init_pos):
                sat_coord = nnt.eph2pos(now)
                if sat_elev(init_pos, sat_coord) < elev_mask:
                    # print "\tSatellite %s excluded" % r
                    continue
            sats += [(r, nnt)]
    # Form matrix if N >= 4:
    if len(sats) > 3:
        # observed [iono-free] pseudoranges
        # P = np.array([obs.ionofree_pseudorange(s[0]) for s in sats])        # iono-free
        P = np.array([obs.obs_data['C1'][obs.prn(s[0])] for s in sats])     # C1 only
        # get XYZ-coords of satellites
        XYZs = np.array([s[1].eph2pos(now) for s in sats])  # len(XYZs[0]) = 3
        # print "XYZs =",XYZs
    # elif len(sats) <= 3 and len(init_pos):  # FIXME: rewise this logic
    elif len(sats) <= 3:  # FIXME: rewise this logic
        print("\n\tWarning: too few satellites: {}".format(len(sats)))
        return None
    # else:
    #     print "\n\tWarning: bad measurement!"
    #     print "sats:", sats, init_pos
    #     return None
    # if err == {}: err = {s[0]:0. for s in sats}
    xyzt = [1e-10, 1e-10, 1e-10, 0.]   # initial point
    if len(init_pos):
        xyzt = init_pos + [0.]
    # print "initial position:", lla_string(ecef_to_lat_lon_alt(xyzt)), tuple(xyzt[:3])
    for itr in range(10):
        # print "\n*** iter = %d ***" % itr
        # geometrical ranges
        lla = ecef_to_lat_lon_alt(xyzt, deg=False)
        ## rho is geometric ranges i.e. distances between current position and every satellite in `sats`
        rho = np.array([np.sqrt(sum([(x - xyzt[i]) ** 2 for i, x in enumerate(XYZs[j])])) for j in range(len(sats))])
        # print "ρ =", rho
        # form l-vector (sometimes `l` is denoted as `b`)
        # print "cδt =", xyzt[3]
        # l = np.matrix([P[i] - rho[i] + c * s[1].time_offset(now + dt.timedelta(seconds=xyzt[3]))
        # print "time_offset(now + xyzt[3]) =",[s[1].time_offset(now + xyzt[3]) for s in sats]
        l = np.matrix([P[i] - rho[i] + c * s[1].time_offset(now + xyzt[3])
                       - tropmodel(lla, sat_elev(xyzt[:3], XYZs[i], deg=False), vmf_coeffs)
                       for i, s in enumerate(sats)]).transpose()
        # from A-matrix
        A = np.matrix([np.append((xyzt[:3] - XYZs[i]) / rho[i], [c]) for i in range(len(sats))])
        AT = A.transpose()
        # form x-vector
        x_hat_matrix = ((AT * A).I * AT * l)
        x_hat = x_hat_matrix.flatten().getA()[0]
        x_hat[3] /= c
        # x_hat[3] *= 1e9    # time in seconds again
        # print "(x,y,z,cδt) ="," m, ".join(map(lambda x: "%.f" %x, x_hat[:3])),"m, %.1e" % x_hat[3]
        xyzt += x_hat
        # print lla_string(ecef_to_lat_lon_alt(xyzt)),"%.4f"%xyzt[3]
        delta = np.sqrt(sum([k ** 2 for k in x_hat[:3]]))
        if delta < 1.:
            # print "1 meter accuracy achieved, break"
            break
        # now += dt.timedelta(seconds=x_hat[3])
        # XYZs = np.array([s[1].eph2pos(now + dt.timedelta(seconds=x_hat[3])) for s in sats])
        XYZs = np.array([s[1].eph2pos(now + x_hat[3]) for s in sats])

    phi, t, h = ecef_to_lat_lon_alt(xyzt, deg=False)
    R = np.matrix([[-sin(phi) * cos(t), -sin(phi) * sin(t), cos(phi)],
                   [-sin(t), cos(t), 0],
                   [cos(phi) * cos(t), cos(phi) * sin(t), sin(phi)]])
    Q = (AT * A).I
    # S_T = R * Q[0:3, 0:3] * R.transpose()
    # GDOP = sqrt(sum(S_T.diagonal().getA()[0]) + Q[3, 3])
    # print "GDOP = %.3f, VDOP = %.3f" % (GDOP,sqrt(S_T[2,2]))
    return xyzt[:3]


if __name__ == "__main__":
    filename = 'test.'
    nav_file = '../test_data/%sn' % filename
    glo_file = '../test_data/%sg' % filename
    obs_file = '../test_data/%so' % filename
    obs_file = '../test_data/out.o'
    # obs_file = '../test_data/test.o.full'

    # Process Nav file:
    # ``navigations`` is a dict with
    #       keys:   GNSS identificators, i.e. 'G16', 'G04', ...
    #       values: Nav observation objects
    #   Note: One satellite may have several nav objects (for several times,
    #       e.g. data on 14:00 and on 16:00)
    navigations = parse_rinex(nav_file)
    # navigations = parse_rinex(glo_file)

    # Process Obs file
    observations = parse_rinex(obs_file)
    o = observations[1400]
    print(o.date)

    sat_positions, sat_names = [], []
    user_pos = least_squares(o, navigations)
    for s in navigations:
        n = navigations[s][0]
        xyz = n.eph2pos(n.date)
        sat_positions += [xyz]
        sat_names += [s]
        print("User position: {}".format(ecef_to_lat_lon_alt(user_pos)))
        # print("Satellite's %s zenith angle: %.1f" %
        #       (s, sat_elev(user_pos, xyz))), " %d km" % (distance(xyz,[0.,0.,0.])/1000 -6378)
    # satellites(user_pos, sat_positions, sat_names)
    """
    user_pos = []
    for num_o in range(1, 100, 10):
    # for num_o in range(190, len(observations), 100):
        # print num_o,
        user_pos += [least_squares(observations[num_o], navigations)]
    user_pos = [up[:3] for up in user_pos if up is not None]
    print map(int,map(distance,user_pos[1:],user_pos[:-1]))
    print "User's position:\n",'\n'.join(map(lambda x: lla_string(ecef_to_lat_lon_alt(x)),user_pos))
    home = [2734549.4888, 1595964.1159, 5518311.2380]  # real (approximate) position
    print user_pos
    print "Distance to the real point: %.6f km" % (distance(home, user_pos[-1]) / 1000.)
    # on_map(map(ecef_to_lat_lon_alt, user_pos), scale=1e5)
    """