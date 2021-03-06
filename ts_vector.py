#! /usr/bin/env python

'''
ts_vector

Numerically integrate the dynamics of a hierarchical triple in the test
particle limit.
'''

# Ignore DeprecationWarnings if called from command line
if __name__ == '__main__':
  import __init__

# System packages
import argparse
import sys
import time

# Numerical packages
from math import sin, cos
import numpy as np
from scipy.integrate import ode, quad
from scipy.optimize import root, fsolve

class Triple_vector:
  '''Evolve a triple in time using the vectorial equations of motion.  This
  class only applies to a triple in the test particle approximation.  For
  triples with a massive secondary, use the Triple class.
  
  Parameters:
    a1: Semi-major axis of inner binary in AU
    a2: Semi-major axis of outer binary in AU
    e1: Eccentricity of inner binary
    e2: Eccentricity of outer binary
    inc: Inclination between inner and outer binaries in degrees
    longascnode: Longitude of ascending node in degrees
    argperi: Argument of periapsis of the inner binary in degrees
    m1: Mass of component 1 of the inner binary in solar masses
    m3: Mass of the tertiary in solar masses
    epsoct: epsilon_octupole (without the mass term).  If set, this
      overrides semi-major axis and outer eccentricity settings.
    tstop: The time to integrate in years
    cputstop: The maximum amount of CPU time to integrate in seconds
    outfreq: Print output on every nth step
    outfilename: Write output to this file.  If None, print to stdout.
    atol: Absolute tolerance of the integrator
    rtol: Relative tolerance of the integrator
    integration_algo: The integration algorithm.  See scipy.ode
      documentation
    quadrupole: Include the quadrupole term of the potential
    octupole: Include the octupole term of the potential
  '''

  def __init__(self, a1=1., a2=20., e1=.1, e2=.3, inc=80., longascnode=180.,
    argperi=0., m1=1, m3=1, epsoct=None, tstop=1e3, cputstop=300, outfreq=1,
    outfilename=None, atol=1e-9, rtol=1e-9, integration_algo='vode',
    quadrupole=True, octupole=True):

    # Given parameters
    self.a1 = float(a1)
    self.a2 = float(a2)
    self.e1 = e1
    self.e2 = e2
    self.inc = inc * np.pi / 180
    self.Omega = longascnode * np.pi / 180
    self.g1 = argperi * np.pi / 180
    self.m1 = m1
    self.m2 = 0 # Assume a test particle
    self.m3 = m3
    self._t = 0

    self.quadrupole = quadrupole
    self.octupole = octupole

    # Derived parameters
    self.th = np.cos(self.inc)
    self.j = np.sqrt(1 - self.e1**2)

    if epsoct is None:
      self.epsoct = self.e2 / (1 - self.e2**2) * (self.a1 / self.a2)
    else:
      self.epsoct = epsoct
      self.e2 = None
      self.a1 = None
      self.a2 = None

    # Integrals of motion
    self.Hhatquad = ((2 + 3 * self.e1**2) * (1 - 3 * self.th**2) - 15 *
      self.e1**2 * (1 - self.th**2) * np.cos(2 * self.g1))

    # The vectorial elements
    self.jhatvec = np.array([
      sin(self.inc) * sin(self.Omega),
      -sin(self.inc) * cos(self.Omega),
      cos(self.inc)])
    self.jvec = self.j * self.jhatvec

    ehatvec_sol = root(_evec_root, [.5, .5, .5], (self.jhatvec, self.g1))
    self.ehatvec = ehatvec_sol.x
    self.evec = self.e1 * self.ehatvec

    # Elements of the potential
    self.Phi0 = 4 * np.pi**2 * self.m3 * self.a1**2 / (self.a2**3 * (1 -
      self.e2**2)**(3/2.))
    self.phiq = 0
    if self.quadrupole:
      self.phiq += 3/4. * (self.jvec[2]**2 / 2. + self.e1**2 - 5/2. *
        self.evec[2]**2 - 1/6.)
    self.phioct = 0
    if self.octupole:
      self.phioct += self.epsoct * 75/64. * (self.evec[0] * (1/5. - 8/5. *
        self.e1**2 + 7 * self.evec[2]**2 - self.jvec[2]**2) - 2 *
        self.evec[2] * self.jvec[0] * self.jvec[2])
    self.tsec = 2 * np.pi * np.sqrt(self.m1 * self.a1) / self.Phi0

    self.update()

    # Integration parameters
    self.nstep = 0
    self.tstop = tstop
    self.cputstop = cputstop
    self.outfreq = outfreq
    self.outfilename = outfilename
    self.integration_algo = integration_algo
    self.y = list(np.concatenate((self.jvec, self.evec)))

    # We have saved some of the initial values (e.g., jvec_0).  Here we set
    # them to their respective parameters.  (I.e., we set jvec = jvec_0[:].)
    self._save_initial_params()

    if self.outfilename is not None:
      self.outfile = open(self.outfilename, 'w')

    # Set up the integrator
    self.atol = atol
    self.rtol = rtol
    self.solver = ode(self._deriv)
    self.solver.set_integrator(self.integration_algo, nsteps=1, 
      atol=self.atol, rtol=self.rtol)
    self.solver.set_initial_value(self.y, self._t).set_f_params(self.epsoct)
    self.solver._integrator.iwork[2] = -1 # Don't print FORTRAN errors

  def _save_initial_params(self):
    '''Set the variables to their initial values.  Just a clone of
    reset().'''

    # Ordinary variables
    self.e1_0 = self.e1
    self.e2_0 = self.e2
    self.inc_0 = self.inc
    self.Omega_0 = self.Omega
    self.g1_0 = self.g1
    self.j_0 = self.j
    self.nstep = 0

    # Arrays need to be deep copied
    self.jhatvec_0 = self.jhatvec[:]
    self.jvec_0 = self.jvec[:]
    self.ehatvec_0 = self.ehatvec[:]
    self.evec_0 = self.evec[:]

  def reset(self):
    '''Set the variables to their initial values.'''

    # Ordinary variables
    self.e1 = self.e1_0
    self.e2 = self.e2_0
    self.inc = self.inc_0
    self.Omega = self.Omega_0
    self.g1 = self.g1_0
    self.j = self.j_0
    self.nstep = 0
    self._t = 0

    # Arrays need to be deep copied
    self.jhatvec = self.jhatvec_0[:]
    self.jvec = self.jvec_0[:]
    self.ehatvec = self.ehatvec_0[:]
    self.evec = self.evec_0[:]

    self.update()

  def update(self):
    '''Update the derived parameters.'''
    self.calc_Th()
    self.calc_CKL()
    self.t = self._t * self.tsec
    self.e1 = np.linalg.norm(self.evec)
  
  def calc_Th(self):
    '''Calculate Kozai's integral.'''
    self.Th = (1 - self.e1**2) * np.cos(self.inc)**2

  def calc_CKL(self):
    '''Calculate the libration constant.'''
    self.CKL = (self.e1**2 * (1 - 5./2 * np.sin(self.inc)**2 *
      np.sin(self.g1)**2))

  def _deriv(self, t, y, epsoct):
    '''The EOMs.  See Eqs. 4 of Katz et al. (2011).'''

    # Note that we have the following correspondences:
    # y[0]  y[1]  y[2]  y[3]  y[4]  y[5]
    # j_x   j_y   j_z   e_x   e_y   e_z

    #The total eccentricity:
    jx, jy, jz, ex, ey, ez = y
    e_sq = ex**2 + ey**2 + ez**2

    # Calculate the derivatives of phi.
    grad_j_phi_q = np.array([0, 0, 3/4. * jz])
    grad_j_phi_oct = -75/32. * np.array([ez * jz, 0,
      ex * jz + ez * jx])
    grad_e_phi_q = np.array([3/2. * ex, 3/2. * ey, -9/4. * ez])
    grad_e_phi_oct = np.array([
      75/64. * (1/5. - 8/5. * e_sq + 7 * ez**2 - jz**2) - 15/4. * ex**2,
      -15/4. * ex * ey,
      75/64. * (54/5. * ex * ez - 2 * jx * jz)])

    grad_j_phi = grad_j_phi_q + epsoct * grad_j_phi_oct
    grad_e_phi = grad_e_phi_q + epsoct * grad_e_phi_oct

    djdtau = np.cross(y[:3], grad_j_phi) + np.cross(y[3:], grad_e_phi)
    dedtau = np.cross(y[:3], grad_e_phi) + np.cross(y[3:], grad_j_phi)

    ret = np.concatenate((djdtau, dedtau))
    return list(ret)

  def _step(self):
    self.solver.integrate(self.tstop, step=True)
    self.nstep += 1
    self._t = self.solver.t
    self.jvec = self.solver.y[:3]
    self.evec = self.solver.y[3:]
    self.update()

  def integrate(self):
    '''Integrate the triple in time.'''
    self.printout()
    self.tstart = time.time()
    while ((self.t < self.tstop) and 
      (time.time() - self.tstart < self.cputstop)):

      self._step()
      if self.nstep % self.outfreq == 0:
        self.printout()

    self.printout()
    if self.outfilename is not None:
      self.outfile.close()

  def ecc_extrema(self):
    '''Integrate the triple, but only print out on eccentricity extrema.'''
    t_prev = 0
    e_prev = 0
    e_prev2 = 0
    while self.t < self.tstop:
      self._step()
      e = np.linalg.norm(self.evec)
      if e_prev2 < e_prev > e:
        outstring = ' '.join(map(str, [t_prev, e_prev]))
        if self.outfilename is None:
          print outstring
        else:
          self.outfile.write(outstring + '\n')
      t_prev = self.t
      e_prev2 = e_prev
      e_prev = e

    if self.outfilename is not None:
      self.outfile.close()

  def printflips(self):
    '''Integrate the triple, but print out only when there is a flip.'''
    t_prev = 0
    e_prev = 0
    e_prev2 = 0
    sign_prev = np.sign(self.jvec[2])
    while self.t < self.tstop:
      self._step()
      e = np.linalg.norm(self.evec)
      if e_prev2 < e_prev > e:
        if np.sign(self.jvec[2]) != sign_prev:
          outstring = ' '.join(map(str, [t_prev, e_prev]))
          if self.outfilename is None:
            print outstring
          else:
            self.outfile.write(outstring + '\n')
        sign_prev = np.sign(self.jvec[2])
      t_prev = self.t
      e_prev2 = e_prev
      e_prev = e
    self.outfile.close()

  def printout(self):
    '''Print out the state of the system in the format:

    time  jx  jy  jz  ex  ey  ez

    '''

    outstring = ' '.join(map(str, np.concatenate((np.array([self.t]), 
      self.jvec, self.evec))))
    if self.outfilename is None:
      print outstring
    else:
      self.outfile.write(outstring + '\n')

  def flip_times(self, nflips=3):
    '''Find the times that the inner binary flips.'''
    sign = np.sign(self.jvec[2])
    sign_prev = sign
    flip_count = 0

    # Integrate along...
    while flip_count < nflips:
      self._step()
      sign = np.sign(self.jvec[2])
      if sign != sign_prev:
        flip_count += 1
        self.printout()
      sign_prev = sign

  def flip_period(self, nflips=3):
    '''Return the period of flips.'''
    sign = np.sign(self.jvec[2])
    sign_prev = sign
    flip_count = 0
    fliptime_prev = 0
    periods = []

    # Integrate along...
    while len(periods) < nflips:
      self._step()
      sign = np.sign(self.jvec[2])
      if sign != sign_prev:
        if fliptime_prev != 0:
          periods.append(self.t - fliptime_prev)
        fliptime_prev = self.t
      sign_prev = sign

    return np.mean(periods)

  def __exit__(self):
    try:
      self.outfile.close()
    except NameError:
      pass


def _evec_root(x, j, g1):
  '''The set of equations that determine evec.'''

  # Orthogonal to j
  cond1 = x[0] * j[0] + x[1] * j[1] + x[2] * j[2]

  # Normalized
  cond2 = x[0]**2 + x[1]**2 + x[2]**2 - 1.

  # Gives the right argument of periapsis
  crossnorm = np.sqrt(j[0]**2 + j[1]**2)
  cond3 = x[0] * j[1] / crossnorm - x[1] * j[0] / crossnorm + cos(g1)

  return [cond1, cond2, cond3]

def process_command_line(argv):
  '''Process the command line.'''
  
  if argv is None:
    argv = sys.argv[1:]

  # Configure the command line options
  parser = argparse.ArgumentParser()

  def_trip = Triple_vector()
  parser.add_argument('-m', '--m1', dest='m1', type=float, 
    default=def_trip.m1, help = 
    'Mass of star 1 in inner binary in solar masses [%g]' % def_trip.m1,
    metavar='\b')
  parser.add_argument('-o', '--m3', dest='m3', type=float, 
    default=def_trip.m3, help = 
    'Mass of tertiary in solar masses [%g]' % def_trip.m3, metavar='\b')
  parser.add_argument('-a', '--a1', dest='a1', type=float, 
    default=def_trip.a1, help = 
    'Inner semi-major axis in au [%g]' % def_trip.a1, metavar='\b')
  parser.add_argument('-b', '--a2', dest='a2', type=float, 
    default=def_trip.a2, help = 
    'Outer semi-major axis in au [%g]' % def_trip.a2, metavar='\b')
  parser.add_argument('-g', '--g1', dest='g1', type=float, 
    default=def_trip.g1, help = 
    'Inner argument of periapsis in degrees [%g]' % (def_trip.g1 * 180 /
      np.pi), metavar='\b')
  parser.add_argument('-L', '--Omega', dest='Omega', type=float, 
    default=def_trip.Omega, help = 
    'Longitude of ascending node in degrees [%g]' % def_trip.Omega,
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
  parser.add_argument('-t', '--end', dest='tstop', type=float, 
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
  parser.add_argument('--epsoct', dest='epsoct', type=float, help = 
    'Set epsilon_octupole parameter (override SMA and e2 settings)',
    metavar='\b')
  parser.add_argument('--noquad', dest='quad', action='store_false',
    default=def_trip.quadrupole, help = 'Turn off quadrupole terms')
  parser.add_argument('--nooct', dest='oct', action='store_false',
    default=def_trip.octupole, help = 'Turn off octupole terms')
  parser.add_argument('--algorithm', dest='algo', type=str,
    default=def_trip.integration_algo, help = 'Integration algorithm [%s]' 
    % def_trip.integration_algo)

  arguments = parser.parse_args()
  return arguments

def main(argv=None):
  args = process_command_line(argv)
  tv = Triple_vector(a1=args.a1, a2=args.a2, e1=args.e1, e2=args.e2, 
        inc=args.inc, longascnode=args.Omega, argperi=args.g1, m1=args.m1,
        m3=args.m3, epsoct=args.epsoct, tstop=args.tstop, 
        cputstop=args.cputstop, outfreq=args.outfreq, atol=args.atol, 
        rtol=args.rtol, integration_algo=args.algo, quadrupole=args.quad,
        octupole=args.oct)

  tv.integrate()
  return 0

if __name__=='__main__':
  status = main()
  sys.exit(status)
