#! /usr/bin/env python 

'''
triplesec

Numerically integrate the dynamics of a hierarchical triple.
'''

# Ignore DeprecationWarnings if called from command line
if __name__ == '__main__':
  import __init__

# System modules
import argparse
import json
import random
import sys
import time

# Numerical modules
from math import sqrt, cos, sin, pi, acos
import numpy as np
from scipy.integrate import ode, quad
from scipy.optimize import root, fsolve
from ts_constants import *

class Triple:
  '''Evolve a hierarchical triple using the Hamiltonian equations of motion.
  This class handles triples in which all objects are massive.  To integrate
  in the test particle approximation use the Triple_vector class.

  Parameters:
    a1: Semi-major axis of inner binary in AU
    a2: Semi-major axis of outer binary in AU
    e1: Eccentricity of inner binary
    e2: Eccentricity of outer binary
    inc: Inclination between inner and outer binaries in degrees
    argperi1: Argument of periapsis of the inner binary in degrees
    argperi2: Argument of periapsis of the outer binary in degrees
    m1: Mass of component 1 of the inner binary in solar masses
    m2: Mass of component 2 of the inner binary in solar masses
    m3: Mass of the tertiary in solar masses
    r1: Radius of component 1 of the inner binary in solar radii
    r2: Radius of component 2 of the inner binary in solar radii
    epsoct: epsilon_octupole (without the mass term).  If set, this
      overrides semi-major axis and outer eccentricity settings.
    tstop: The time to integrate in years
    cputstop: The maximum amount of CPU time to integrate in seconds
    outfreq: Print output on every nth step
    outfilename: Write output to this file.  If None, print to stdout.
    atol: Absolute tolerance of the integrator
    rtol: Relative tolerance of the integrator
    quadrupole: Include the quadrupole term of the Hamiltonian
    octupole: Include the octupole term of the Hamiltonian
    hexadecapole: Include the hexadecapole term of the Hamiltonian
    gr: Include post-Newtonian terms in the equations of motion
    integration_algo: The integration algorithm.  See scipy.ode
      documentation
    print_properties: Print the properties of the triple in JSON format
    properties_outfilename: Filename to which properties will be written.
      If None and print_properties is True, print to stderr.
  '''

  def __init__(self, a1=1, a2=20, e1=.1, e2=.3, inc=80, argperi1=0, 
    argperi2=0, m1=1., m2=1., m3=1., r1=0, r2=0, epsoct=None, tstop=1e3,
    cputstop=300, outfreq=1, outfilename=None, atol=1e-9, rtol=1e-9,
    quadrupole=True, octupole=True, hexadecapole=False, gr=False,
    integration_algo='vode', print_properties=False,
    properties_outfilename=None):

    self.a1 = float(a1)
    self.a2 = float(a2)
    self.e1 = e1
    self.e2 = e2
    self.inc = inc * np.pi / 180
    self.g1 = argperi1 * np.pi / 180
    self.g2 = argperi2 * np.pi / 180
    self.m1 = float(m1)
    self.m2 = float(m2)
    self.m3 = float(m3)
    self.r1 = float(r1)
    self.r2 = float(r2)
    self.tstop = tstop
    self.cputstop = cputstop
    self.outfreq = outfreq
    self.print_properties = print_properties
    self.properties_outfilename = properties_outfilename

    # Derived parameters
    if epsoct is None:
      self.epsoct = self.e2 / (1 - self.e2**2) * self.a1 / self.a2
    else:
      self.epsoct = epsoct
      self.e2 = None
      self.a1 = None
      self.a2 = None

    # Unit conversions
    self.t = 0
    self._t = 0
    self.th = np.cos(self.inc)
    self._m1 = self.m1 * M_sun
    self._m2 = self.m2 * M_sun
    self._m3 = self.m3 * M_sun
    self._a1 = self.a1 * au
    self._a2 = self.a2 * au

    self.quadrupole = quadrupole
    self.octupole = octupole
    self.hexadecapole = hexadecapole
    self.gr = gr
    if self.e2 == 0:
      self.octupole = False

    self.calc_C()
    self.calc_G1()
    self.calc_G2()
    self._H = np.sqrt(2 * self._G1 * self._G2 * self.th + self._G1**2 +
      self._G2**2)
    self.update()

    self.outfilename = outfilename
    if self.outfilename is not None:
      self.outfile = open(self.outfilename, 'w')

    # Integration parameters
    self.nstep = 0
    self.atol = atol
    self.rtol = rtol
    self.collision = False # Has a collision occured?

    if self.properties_outfilename is not None:
      self.ts_printjson()

    self.integration_algo = integration_algo
    self._y = [self._a1, self.e1, self.g1, self.e2, self.g2, self._H]

    # Set up the integrator
    self.solver = ode(self._deriv)
    self.solver.set_integrator(self.integration_algo, nsteps=1, atol=atol,
      rtol=rtol)
    self.solver.set_initial_value(self._y, self._t)
    if self.integration_algo == 'vode':
      self.solver._integrator.iwork[2] = -1 # Don't print FORTRAN errors

  def calc_cosphi(self):
    '''Calculate the angle between periastron directions.  See Eq. 23 of
    Blaes et al. (2002).'''

    self.cosphi = (-cos(self.g1) * cos(self.g2) - self.th * sin(self.g1) *
      sin(self.g2))

  def calc_G1(self):
    '''Calculate G1.  See Eq. 6 of Blaes et al. (2002).'''
    self._G1 = (self._m1 * self._m2 * np.sqrt(G * self._a1 * (1 -
      self.e1**2) / (self._m1 + self._m2)))

  def calc_G2(self):
    '''Calculate G2.  See Eq. 7 of Blaes et al. (2002).'''
    self._G2 = ((self._m1 + self._m2) * self._m3 * np.sqrt(G * self._a2 * (1
      - self.e2**2) / (self._m1 + self._m2 + self._m3)))

  def calc_C(self):
    '''Calculate C2 and C3.  Eqs. 18 & 19 of BLaes et al. (2002)'''

    if self.quadrupole:
      self.C2 = (G * self._m1 * self._m2 * self._m3 / (16 * (self._m1 +
        self._m2) * self._a2 * (1 - self.e2**2)**(3./2)) * (self._a1 /
        self._a2)**2)
    else:
      self.C2 = 0

    if self.octupole:
      self.C3 = (15 * G * self._m1 * self._m2 * self._m3 * (self._m1 -
        self._m2) / (64 * (self._m1 + self._m2)**2 * self._a2 * (1 -
        self.e2**2)**(5./2)) * (self._a1 / self._a2)**3)
    else:
      self.C3 = 0

  def calc_th(self):
    '''Calculate the cosine of the inclination.  See Eq. 22 of Blaes et al.
    (2002).'''

    self.th = ((self._H**2 - self._G1**2 - self._G2**2) / (2 * self._G1 *
      self._G2))

  def calc_cosi(self):
    '''Calculate cos i.  A synonym for calc_th.'''
    self.calc_th()

  def update(self):
    '''Update the derived parameters of the triple after a step.'''
    self.calc_C()
    self.calc_cosphi()
    self.calc_G1()
    self.calc_G2()
    self.calc_th()

    self.inc = acos(self.th) * 180 / np.pi
    self.a1 = self._a1 / au
    self.t = self._t / yr2s

  def _deriv(self, t, y):
    '''The EOMs.  See Eqs. 11 -- 17 of Blaes et al. (2002).'''

    # Unpack the values
    a1, e1, g1, e2, g2, H = y

    # Calculate trig functions only once
    sing1 = sin(g1)
    sing2 = sin(g2)
    cosg1 = cos(g1)
    cosg2 = cos(g2)

    m1 = self._m1
    m2 = self._m2
    m3 = self._m3
    a2 = self._a2
  
    G1 = m1 * m2 * np.sqrt(G * a1 * (1 - e1**2) / (m1 + m2))
    G2 = (m1 + m2) * m3 * np.sqrt(G * a2 * (1 - e2**2) / (m1 + m2 + m3))

    C2 = (G * m1 * m2 * m3 / (16 * (m1 + m2) * a2 * (1 - e2**2)**(3./2)) * 
          (a1 / a2)**2)
    C3 = (15 * G * m1 * m2 * m3 * (m2 - m1) / (64 * (m1 + m2)**2 * a2 *
          (1 - e2**2)**(5./2)) * (a1 / a2)**3)

    th = (H**2 - G1**2 - G2**2) / (2 * G1 * G2)
    cosphi = cosg1 * cosg2 - th * sing1 * sing2
    B = 2 + 5 * e1**2 - 7 * e1**2 * cos(2 * g1)
    A = 4 + 3 * e1**2 - 5 / 2. * (1 - th**2) * B

    # Eq. 11 of Blaes et al. (2002)
    da1dt = 0
    if self.gr:
      da1dt += -(64 * G**3 * m1 * m2 * (m1 + m2) / (5 * c**5 * a1**3 * 
        sqrt((1 - e1**2)**7)) * (1 + 73 / 24. * e1**2 + 37 / 96. * e1**4))

    # Eq. 12 of Blaes et al. (2002)
    dg1dt = 0
    if self.quadrupole:
      dg1dt += (6 * C2 * (1 / G1 * (4 * th**2 + (5 * cos(2 * g1) - 1) * (1 -
        e1**2 - th**2)) + th / G2 * (2 + e1**2 * (3 - 5 * cos(2 * g1)))))
    if self.octupole:
      dg1dt += (C3 * e2 * e1 * (1 / G2 + th / G1) * (sing1 * sing2 * 
        (A + 10 * (3 * th**2 - 1) * (1 - e1**2)) - 5 * th * B * cosphi) - C3
        * e2 * (1 - e1**2) / (e1 * G1) * (10 * th * (1 - th**2) * (1 - 3 *
        e1**2) * sing1 * sing2 + cosphi * (3 * A - 10 * th**2 + 2)))
    if self.gr:
      dg1dt += ((3 / (c**2 * a1 * (1 - e1**2)) * 
        sqrt((G * (m1 + m2) / a1)**3)))
    if self.hexadecapole:
      dg1dt += (1 / (4096. * a2**5 * sqrt(1 - e1**2) * (m1 + m2)**5) * 45 *
        a1**3 * sqrt(a1 * G * (m1 + m2)) * (-1 / ((e2**2 - 1)**4 * sqrt(a2 *
        G * (m1 + m2 + m3))) * (m1**2 - m1 * m2 + m2**2) * (sqrt(1 - e2**2) *
        m2**2 * m3 * sqrt(a2 * G * (m1 + m2 + m3)) * th + m1**2 * (sqrt( 1 -
        e1**2) * m2 * sqrt(a1 * G * (m1 + m2)) + sqrt(1 - e2**2) * m3 *
        sqrt(a2 * G * (m1 + m2 + m3)) * th) + m1 * m2 * (sqrt(1 - e1**2) * m2
        * sqrt(a1 * G * (m1 + m2)) + sqrt(1 - e1**2) * sqrt(a1 * G * (m1
        + m2)) * m3 + 2 * sqrt(1 - e2**2) * m3 * sqrt(a2 * G * (m1 + m2 +
        m3)) * th)) * (96 * th + 480 * e1**2 * th + 180 * e1**4 * th + 144 *
        e2**2 * th + 720 * e1**2 * e2**2 * th + 270 * e1**4 * e2**2 * th -
        224 * th**3 - 1120 * e1**2 * th**3 - 420 * e1**4 * th**3 - 336 *
        e2**2 * th**3 - 1680 * e1**2 * e2**2 * th**3 - 630 * e1**4 * e2**2 *
        th**3 + 56 * e1**2 * (2 + e1**2) * (2 + 3 * e2**2) * th * (7 * th**2
        - 4) * cos(2 * g1) - 294 * e1**4 * (2 + 3 * e2**2) * th * (th**2 - 1)
        * cos(4 * g1) - 147 * e1**4 * e2**2 * cos(4 * g1 - 2 * g2) + 441 *
        e1**4 * e2**2 * th**2 * cos(4 * g1 - 2 * g2) + 294 * e1**4 * e2**2 *
        th**3 * cos(4 * g1 - 2 * g2) + 140 * e1**2 * e2**2 * cos(2 * (g1 -
        g2)) + 70 * e1**4 * e2**2 * cos(2 * (g1 - g2)) + 336 * e1**2 * e2**3
        * th * cos(2 * (g1 - g2)) + 168 * e1**4 * e2**2 * th * cos(2 * (g1 -
        g2)) - 588 * e1**2 * e2**2 * th**2 * cos(2 * (g1 - g2)) - 294 * e1**4
        * e2**2 * th**2 * cos(2 * (g1 - g2)) - 784 * e1**2 * e2**2 * th**3 *
        cos(2 * (g1 - g2)) - 392 * e1**4 * e2**2 * th**3 * cos(2 * (g1 - g2))
        - 128 * e2**2 * th * cos(2 * g2) - 640 * e1**2 * e2**2 * th * cos(2 *
        g2) - 240 * e1**4 * e2**2 * th * cos(2 * g2) + 224 * e2**2 * th**3 *
        cos(2 * g2) + 1120 * e1**2 * e2**2 * th**3 * cos(2 * g2) + 420 *
        e1**4 * e2**2 * th**3 * cos(2 * g2) - 140 * e1**2 * e2**2 * cos(2 *
        (g1 + g2)) - 70 * e1**4 * e2**2 * cos(2 * (g1 + g2)) + 336 * e1**2 *
        e2**2 * th * cos(2 * (g1 + g2)) + 168 * e1**4 * e2**2 * th * cos(2 *
        (g1 + g2)) + 588 * e1**2 * e2**2 * th**2 * cos(2 * (g1 + g2)) + 294 *
        e1**4 * e2**2 * th**2 * cos(2 * (g1 + g2)) - 784 * e1**2 * e2**2 *
        th**3 * cos(2 * (g1 + g2)) - 392 * e1**4 * e2**2 * th**3 * cos(2 *
        (g1 + g2)) + 147 * e1**4 * e2**2 * cos(2 * (2 * g1 + g2)) - 441 *
        e1**4 * e2**2 * th**2 * cos(2 * (2 * g1 + g2)) + 294 * e1**4 * e2**2
        * th**3 * cos(2 * (2 * g1 + g2))) + 1 / (e1 * sqrt((1 - e2**2)**7)) *
        2 * (1 - e1**2) * (m1 + m2) * (m1**3 + m2**3) * m3 * (e1 * (4 + 3 *
        e1**2) * (2 + 3 * e2**2) * (3 - 30 * th**2 + 35 * th**4) - 28 * (e1
        + e1**3) * (2 + 3 * e2**2) * (1 - 8 * th**2 + 7 * th**4) * cos(2 *
        g1) + 147 * e1**3 * (2 + 3 * e2**2) * (th**2 - 1)**2 * cos(4 * g1) -
        10 * e1 * (4 + 3 * e1**2) * e2**2 * (1 - 8 * th**2 + 7 * th**4) *
        cos(2 * g2) + 28 * (e1 + e1**3) * e2**2 * ((1 + th)**2 * (1 - 7 * th
        + 7 * th**2) * cos(2 * (g1 - g2)) + (th - 1)**2 * (1 + 7 * th + 7 *
        th**2) * cos(2 * (g1 + g2))) - 147 * e1**3 * e2**2 * (th**2 - 1) *
        ((1 + th)**2 * cos(4 * g1 - 2 * g2) + (th - 1)**2 * cos(2 * (2 * g1 +
        g2))))))

    # Eq. 13 of Blaes et al. (2002)
    de1dt = 0
    if self.quadrupole:
      de1dt += (30 * C2 * e1 * (1 - e1**2) / G1 * (1 - th**2) * sin(2 * g1))
    if self.octupole:
      de1dt += (-C3 * e2 * (1 - e1**2) / G1 * (35 * cosphi * (1 - th**2) * 
        e1**2 * sin(2 * g1) - 10 * th * (1 - e1**2) * (1 - th**2) * 
        cosg1 * sing2 - A * (sing1 * cosg2 - th * cosg1 * sing2)))
    if self.gr:
      de1dt += (-304 * G**3 * m1 * m2 * (m1 + m2) * e1 / (15 * c**4 * a1**4 * 
        sqrt((1 - e1**2)**5)) * (1 + 121 / 304. * e1**2))
    if self.hexadecapole:
      de1dt += (-(315 * a1**3 * e1 * sqrt(1 - e1**2) * sqrt(a1 * G * (m1 +
      m2)) * (m1**2 - m1 * m2 + m2**2) * m3 * (2 * (2 + e1**2) * (2 + 3 *
      e2**2) * (1 - 8 * th**2 + 7 * th**4) * sin(2 * g1) - 21 * e1**2 * (2 +
      3 * e2**2) * (th**2 - 1)**2 * sin(4 * g1) + e2**2 * (21 * e1**2 * (th -
      1) * (1 + th)**3 * sin(4 * g1 - 2 * g2) - 2 * (2 + e1**2) * (1 + th)**2
      * (1 - 7 * th + 7 * th**2) * sin(2 * (g1 - g2)) - (th - 1)**2 * (2 * (2
      + e1**2) * (1 + 7 * th + 7 * th**2) * sin(2 * (g1 + g2)) - 21 * e1**2 *
      (th**2 - 1) * sin(2 * (2 * g1 + g2)))))) / (2048 * a2**5 * sqrt((1 -
      e2**2)**7) * (m1 + m2)**3))

    dg2dt = 0
    if self.quadrupole:
      dg2dt += (3 * C2 * (2 * th / G1 * (2 + e1**2 * (3 - 5 * cos(2 * g1))) + 1
        / G2 * (4 + 6 * e1**2 + (5 * th**2 - 3) * (2 + 3 * e1**2 - 5 * e1**2 *
        cos(2 * g1))))) 
    if self.octupole:
      dg2dt += (-C3 * e1 * sing1 * sing2 * ((4 * e2**2 + 1) / (e2 * G2) * 10 * 
        th * (1 - th**2) * (1 - e1**2) - e2 * (1 / G1 + th / G2) * (A + 10 * 
        (3 * th**2 - 1) * (1 - e1**2))) - C3 * e1 * cosphi * (5 * B * th * 
        e2 * (1 / G1 + th / G2) + (4 * e2**2 + 1) / (e2 * G2) * A))
    if self.hexadecapole:
      dg2dt += ((9 * a1**3 * (-1 / sqrt(1 - e1**2) * 10 * a2 * sqrt(a1 * G *
      (m1 + m2)) * (m1**2 - m1 * m2 + m2**2) * (sqrt(1 - e2**2) * m2**2 * m3
      * sqrt(a2 * G * (m1 + m2 + m3)) + m1**2 * (sqrt(1 - e2**2) * m3 *
      sqrt(a2 * G * (m1 + m2 + m3)) + sqrt(1 - e1**2) * m2 * sqrt(a1 * G *
      (m1 + m2)) * th) + m1 * m2 * (2 * sqrt(1 - e2**2) * m3 * sqrt(a2 * G *
      (m1 + m2 + m3)) + sqrt(1 - e1**2) * m2 * sqrt(a1 * G * (m1 + m2)) * th
      + sqrt(1 - e1**2) * sqrt(a1 * G * (m1 + m2)) * m3 * th)) * (96 * th +
      480 * e1**2 * th + 180 * e1**4 * th + 144 * e2**2 * th + 720 * e1**2 *
      e2**2 * th + 270 * e1**4 * e2**2 * th - 224 * th**3 - 1120 * e1**2 *
      th**3 - 420 * e1**4 * th**3 - 336 * e2**2 * th**3 - 1680 * e1**2 *
      e2**2 * th**3 - 630 * e1**4 * e2**2 * th**3 + 56 * e1**2 * (2 + e1**2)
      * (2 + 3 * e2**2) * th * (7 * th**2 - 4) * cos(2 * g1) - 294 * e1**4 *
      (2 + 3 * e2**2) * th * (th**2 - 1) * cos(4 * g1) - 147 * e1**4 *
      e2**2 * cos(4 * g1 - 2 * g2) + 441 * e1**4 * e2**2 * th**2 *
      cos(4 * g1 - 2 * g2) + 294 * e1**4 * e2**2 * th**3 * cos(4 * g1 - 2 *
      g2) + 140 * e1**2 * e2**2 * cos(2 * (g1 - g2)) + 70 * e1**4 * e2**2 *
      cos(2 * (g1 - g2)) + 336 * e1**2 * e2**2 * th * cos(2 * (g1 - g2)) +
      168 * e1**4 * e2**2 * th * cos(2 * (g1 - g2)) - 588 * e1**2 * e2**2 *
      th**2 * cos(2 * (g1 - g2)) - 294 * e1**4 * e2**2 * th**2 * cos(2 * (g1
      - g2)) - 784 * e1**2 * e2**2 * th**3 * cos(2 * (g1 - g2)) - 392 * e1**4
      * e2**2 * th**3 * cos(2 * (g1 - g2)) - 128 * e2**2 * th * cos(2 * g2) -
      640 * e1**2 * e2**2 * th * cos(2 * g2) - 240 * e1**4 * e2**2 * th *
      cos(2 * g2) + 224 * e2**2 * th**3 * cos(2 * g2) + 1120 * e1**2 * e2**2
      * th**3 * cos(2 * g2) + 420 * e1**4 * e2**2 * th**3 * cos(2 * g2) - 140
      * e1**2 * e2**2 * cos(2 * (g1 + g2)) - 70 * e1**4 * e2**2 * cos(2 * (g1
      + g2)) + 336 * e1**2 * e2**2 * th * cos(2 * (g1 + g2)) + 168 * e1**4 *
      e2**2 * th * cos(2 * (g1 + g2)) + 588 * e1**2 * e2**2 * th**2 * cos(2
      * (g1 + g2)) + 294 * e1**4 * e2**2 * th**2 * cos(2 * (g1 + g2)) - 784 *
      e1**2 * e2**2 * th**3 * cos(2 * (g1 + g2)) - 392 * e1**4 * e2**2 *
      th**3 * cos(2 * (g1 + g2)) + 147 * e1**4 * e2**2 * cos(2 * (2 * g1 +
      g2)) - 441 * e1**4 * e2**2 * th**2 * cos(2 * (2 * g1 + g2)) + 294 *
      e1**4 * e2**2 * th**3 * cos(2 * (2 * g1 + g2))) + a1 * a2 * G * m1 * m2
      * (m1**3 + m2**3) * (m1 + m2 + m3) * (-6 * (8 + 40 * e1**2 + 15 *
      e1**4) * (-1 + e2**2) * (3 - 30 * th**2 + 35 * th**4) + 7 * (8 + 40 *
      e1**2 + 15 * e1**4) * (2 + 3 * e2**2) * (3 - 30 * th**2 + 35 * th**4)
      + 840 * e1**2 * (2 + e1**2) * (-1 + e2**2) * (1 - 8 * th**2 + 7 *
      th**4) * cos(2 * g1) - 980 * e1**2 * (2 + e1**2) * (2 + 3 * e2**2) * (1
      - 8 * th**2 + 7 * th**4) * cos(2 * g1) - 4410 * e1**4 * (-1 + e2**2) *
      (-1 + th**2)**2 * cos(4 * g1) + 5145 * e1**4 * (2 + 3 * e2**2) * (-1 +
      th**2)**2 * cos(4 * g1) - 70 * (8 + 40 * e1**2 + 15 * e1**4) * e2**2 *
      (1 - 8 * th**2 + 7 * th**4) * cos(2 * g2) + 20 * (8 + 40 * e1**2 + 15 *
      e1**4) * (-1 + e2**2) * (1 - 8 * th**2 + 7 * th**4) * cos(2 * g2) + 980
      * e1**2 * (2 + e1**2) * e2**2 * ((1 + th)**2 * (1 - 7 * th + 7 * th**2)
      * cos(2 * (g1 - g2)) + (-1 + th)**2 * (1 + 7 * th + 7 * th**2) * cos(2
      * (g1 + g2))) - 280 * e1**2 * (2 + e1**2) * (-1 + e2**2) * ((1 + th)**2
      * (1 - 7 * th + 7 * th**2)  * cos(2 * (g1 - g2)) + (-1 + th)**2 * (1 +
      7 * th + 7 * th**2) * cos(2 * (g1 + g2))) - 1470 * e1**4 * (1 - e2**2)
      * (-1 + th) * (1 + th) * ((1 + th)**2 * cos(4 * g1 - 2 * g2) + (-1 +
      th)**2 * cos(2 * (2 * g1 + g2))) - 5145 * e1**4 * e2**2 * (-1 + th**2)
      * ((1 + th)**2 * cos(4 * g1 - 2 * g2) + (-1 + th)**2 * cos(2 * (2 * g1
      + g2)))))) / (8192 * a2**6 * (-1 + e2**2)**4 * (m1 + m2)**5 * sqrt(a2 *
      G * (m1 + m2 + m3))))

    # Eq. 16 of Blaes et al. (2002)
    de2dt = 0
    if self.octupole:
      de2dt += (C3 * e1 * (1 - e2**2) / G2 * (10 * th * (1 - th**2) * (1 -
      e1**2) * sing1 * cosg2 + A * (cosg1 * sing2 - th * sing1 * cosg2)))
    if self.hexadecapole:
      de2dt += ((45 * a1**4 * e2 * m1 * m2 * (m1**2 - m1 * m2 + m2**2) *
        sqrt(a2 * G * (m1 + m2 + m3)) * (-147 * e1**4 * (-1 + th) * (1 +
        th)**3 * sin(4 * g1 - 2 * g2) + 28 * e1**2 * (2 + e1**2) * (1 +
        th)**2 * (1 - 7 * th + 7 * th**2) * sin(2 * (g1 - g2)) + (-1 + th) *
        (2 * (8 + 40 * e1**2 + 15 * e1**4) * (-1 - th + 7 * th**2 + 7 *
        th**3) * sin(2 * g2) - 7 * e1**2 * (-1 + th) * (4 * (2 + e1**2) * (1
        + 7 * th + 7 * th**2) * sin(2 * (g1 + g2)) - 21 * e1**2 * (-1 + th**2)
        * sin(2 * (2 * g1 + g2))))) / (4096 * a2**6 * (-1 + e2**2)**3 * (m1 +
        m2)**4)))

    # Eq. 17 of Blaes et al. (2002)
    dHdt = 0
    if self.gr:
      dHdt += (-32 * G**3 * m1**2 * m2**2 / (5 * c**5 * a1**3 * 
        (1 - e1**2)**2) * sqrt(G * (m1 + m2) / a1) * (1 + 7 / 8. * e1**2) * 
        (G1 + G2 * th) / H)

    der = [da1dt, de1dt, dg1dt, de2dt, dg2dt, dHdt]
    return der

  def _step(self):
    self.solver.integrate(self.tstop, step=True)
    self.nstep += 1
    self._t = self.solver.t
    self.a1, self.e1, self.g1, self.e2, self.g2, self._H = self.solver.y
    self.g1 %= (2 * np.pi)
    self.g2 %= (2 * np.pi)
    self.update()

  def integrate(self):
    '''Integrate the triple in time.'''
    self.ts_printout()
    self.tstart = time.time()
    while ((self.t < self.tstop) and 
      ((time.time() - self.tstart) < self.cputstop)):

      self._step()
      if self.nstep % self.outfreq == 0:
        self.ts_printout()

      if self.a1 * (1 - self.e1) < self.r1 + self.r2:
        self.collision = True
        break

    self.ts_printout()
    if self.outfilename is not None:
      self.outfile.close()

  def ecc_extrema(self):
    '''Integrate the triple, but only print out on eccentricity extrema.'''
    t_prev = 0
    e_prev = 0
    e_prev2 = 0
    self.tstart = time.time()
    while (self.t < self.tstop and 
      time.time() - self.tstart < self.cputstop):
      self._step()
      if e_prev2 < e_prev > self.e1:
        outstring = ' '.join(map(str, [t_prev, e_prev]))
        if self.outfilename is None:
          print outstring
        else:
          self.outfile.write(outstring + '\n')
      t_prev = self.t
      e_prev2 = e_prev
      e_prev = self.e1

  def printflips(self):
    '''Integrate the triple, but print out only when there is a flip.'''
    t_prev = 0
    e_prev = 0
    e_prev2 = 0
    sign_prev = np.sign(self.th)
    while self.t < self.tstop:
      self._step()
      if e_prev2 < e_prev > self.e1:
        if np.sign(self.th) != sign_prev:
          outstring = ' '.join(map(str, [t_prev, e_prev]))
          if self.outfilename is None:
            print outstring
          else:
            self.outfile.write(outstring + '\n')
        sign_prev = np.sign(self.th)
      t_prev = self.t
      e_prev2 = e_prev
      e_prev = e
    self.outfile.close()

  def ts_printout(self):
    '''Print out the state of the system in the format:
      
      t  a1  e1  g1  e2  g2  inc (deg)
      
    '''

    outstring = ' '.join(map(str, [self.t, self.a1, self.e1, 
      self.g1, self.e2, self.g2, self.inc]))

    if self.outfilename is None:
      print outstring
    else:
      self.outfile.write(outstring + '\n')

  def ts_printjson(self):
    '''Print out the initial values in JSON format.'''

    json_data = self.__dict__
    outstring = json.dumps(json_data, sort_keys=True, indent=2)
    if self.properties_outfilename == 'stderr':
      print >> sys.stderr, outstring
    else:
      with open(self.properties_outfilename, 'w') as p_outfile:
        p_outfile.write(outstring)

def process_command_line(argv):
  '''Process the command line.'''
  
  if argv is None:
    argv = sys.argv[1:]

  # Configure the command line options
  parser = argparse.ArgumentParser()

  def_trip = Triple()
  parser.add_argument('-m', '--m1', dest='m1', type=float, 
    default=def_trip.m1, help = 
    'Mass of star 1 in inner binary in solar masses [%g]' % def_trip.m1,
    metavar='\b')
  parser.add_argument('-n', '--m2', dest='m2', type=float, 
    default=def_trip.m2, help = 
    'Mass of star 2 in inner binary in solar masses [%g]' % def_trip.m2,
    metavar='\b')
  parser.add_argument('-o', '--m3', dest='m3', type=float, 
    default=def_trip.m3, help = 
    'Mass of tertiary in solar masses [%g]' % def_trip.m3, metavar='\b')
  parser.add_argument('-r', '--r1', dest='r1', type=float, 
    default=def_trip.r1, help = 
    'Radius of star 1 of the inner binary in R_Sun [%g]' % def_trip.r1,
    metavar='\b')
  parser.add_argument('-s', '--r2', dest='r2', type=float, 
    default=def_trip.r2, help = 
    'Radius of star 2 of the inner binary in R_Sun [%g]' % def_trip.r2,
    metavar='\b')
  parser.add_argument('-a', '--a1', dest='a1', type=float, 
    default=def_trip.a1, help = 
    'Inner semi-major axis in au [%g]' % def_trip.a1, metavar='\b')
  parser.add_argument('-b', '--a2', dest='a2', type=float, 
    default=def_trip.a2, help = 
    'Outer semi-major axis in au [%g]' % def_trip.a2, metavar='\b')
  parser.add_argument('-g', '--g1', dest='g1', type=float, 
    default=def_trip.g1, help = 
    'Inner argument of periapsis in degrees [%g]' % def_trip.g1,
    metavar='\b')
  parser.add_argument('-G', '--g2', dest='g2', type=float, 
    default=def_trip.g2, help = 
    'Outer argument of periapsis in degrees [%g]' % def_trip.g2,
    metavar='\b')
  parser.add_argument('-e', '--e1', dest='e1', type=float, 
    default=def_trip.e1, help = 
    'Inner eccentricity [%g]' % def_trip.e1, metavar='\b')
  parser.add_argument('-f', '--e2', dest='e2', type=float, 
    default=def_trip.e2, help = 
    'Outer eccentricity [%g]' % def_trip.e2, metavar='\b')
  parser.add_argument('-i', '--inc', dest='inc', type=float,
    default=def_trip.inc, help = 
    'Inclination of the third body in degrees [%g]' % def_trip.inc,
    metavar='\b')
  parser.add_argument('-t', '--tstop', dest='tstop', type=float, 
    default=def_trip.tstop, help = 'Total time of integration in years [%g]' 
    % def_trip.tstop, metavar='\b')
  parser.add_argument('-C', '--cpu', dest='cputstop', type=float, 
    default=def_trip.cputstop, help = 
    'cpu time limit in seconds, if -1 then no limit [%g]' %
    def_trip.cputstop, metavar='\b')
  parser.add_argument('-F', '--freq', dest='outfreq', type=int, 
    default=def_trip.outfreq, help = 'Output frequency [%g]' % 
    def_trip.outfreq, metavar='\b')
  parser.add_argument('-A', '--abstol', dest='atol', type=float, 
    default=def_trip.atol, help = 'Absolute accuracy [%g]' % 
    def_trip.atol, metavar='\b')
  parser.add_argument('-R', '--reltol', dest='rtol', type=float, 
    default=def_trip.rtol, help = 'Relative accuracy [%g]' % 
    def_trip.rtol, metavar='\b')
  parser.add_argument('--noquad', dest='quad', action='store_false',
    default=def_trip.quadrupole, help = 'Turn off quadrupole terms')
  parser.add_argument('--nooct', dest='oct', action='store_false',
    default=def_trip.octupole, help = 'Turn off octupole terms')
  parser.add_argument('-c', '--GR', dest='gr', action='store_true', 
    default = def_trip.gr, help = 'Turn on general relativity terms')
  parser.add_argument('-x', '--hex', dest='hex', action='store_true',
    default = def_trip.hexadecapole, help = 'Turn on hexadecapole terms')

  arguments = parser.parse_args()
  return arguments

def main(argv=None):
  args = process_command_line(argv)
  t = Triple(m1=args.m1, m2=args.m2, m3=args.m3, r1=args.r1, r2=args.r2,
        a1=args.a1, a2=args.a2, argperi1=args.g1, argperi2=args.g2,
        e1=args.e1, e2=args.e2, inc=args.inc, tstop=args.tstop,
        cputstop=args.cputstop, outfreq=args.outfreq, atol=args.atol,
        rtol=args.rtol, quadrupole=args.quad, octupole=args.oct,
        hexadecapole=args.hex, gr=args.gr)

  t.integrate()
  return 0

if __name__=='__main__':
  status = main()
  sys.exit(status)
