import numpy as np
import random
from selfdrive.controls.lib.drive_helpers import get_steer_max
from common.numpy_fast import clip
from common.realtime import DT_CTRL
from cereal import log
import common.log as trace1
from selfdrive.ntune import nTune

class LatControlLQR():
  def __init__(self, CP):
    self.trLQR = trace1.Loger("077_R3_LQR_ctrl")    
    self.scale = CP.lateralTuning.lqr.scale
    self.ki = CP.lateralTuning.lqr.ki

    self.A = np.array(CP.lateralTuning.lqr.a).reshape((2, 2))
    self.B = np.array(CP.lateralTuning.lqr.b).reshape((2, 1))
    self.C = np.array(CP.lateralTuning.lqr.c).reshape((1, 2))
    self.K = np.array(CP.lateralTuning.lqr.k).reshape((1, 2))
    self.L = np.array(CP.lateralTuning.lqr.l).reshape((2, 1))
    self.dc_gain = CP.lateralTuning.lqr.dcGain

    self.x_hat = np.array([[0], [0]])
    self.i_unwind_rate = 0.3 * DT_CTRL
    self.i_rate = 1.0 * DT_CTRL

    self.sat_count_rate = 1.0 * DT_CTRL
    self.sat_limit = CP.steerLimitTimer

    self.reset()
    self.tune = nTune(CP, self) # 추가    

  def reset(self):
    self.i_lqr = 0.0
    self.output_steer = 0.0
    self.sat_count = 0.0

  def _check_saturation(self, control, check_saturation, limit):
    saturated = abs(control) == limit

    if saturated and check_saturation:
      self.sat_count += self.sat_count_rate
    else:
      self.sat_count -= self.sat_count_rate

    self.sat_count = clip(self.sat_count, 0.0, 1.0)

    return self.sat_count > self.sat_limit

  def update(self, active, CS, CP, path_plan):
    self.tune.check() # 추가    
    lqr_log = log.ControlsState.LateralLQRState.new_message()

    steers_max = get_steer_max(CP, CS.vEgo)
    torque_scale = (0.45 + CS.vEgo / 60.0)**2  # Scale actuator model with speed

    steering_angle = CS.steeringAngle

    ###  설정값 분석을 위한 랜덤화
    self.ki = random.uniform(0.01, 0.02)    #self.ki - (self.ki*0.5), self.ki + (self.ki*0.5) )
    self.scale = random.random(1700, 1900)     #int(self.scale) - int(self.scale*0.055), int(self.scale) + int(self.scale*0.055) ) )
    self.dc_gain = random.uniform(0.0028, 0.0032 )  #self.dc_gain - (self.dc_gain*0.1), self.dc_gain + (self.dc_gain*0.1) )    
    ########################### 

    v_ego_kph = CS.vEgo * 3.61
    #self.ki, self.scale = self.atom_tune( v_ego_kph, CS.steeringAngle, CP )
    log_ki = self.ki
    log_scale = self.scale
    log_dc_gain = self.dc_gain    

    # Subtract offset. Zero angle should correspond to zero torque
    self.angle_steers_des = path_plan.angleSteers - path_plan.angleOffset
    log_angle_steers_des =  self.angle_steers_des
    steering_angle -= path_plan.angleOffset

    # Update Kalman filter
    angle_steers_k = float(self.C.dot(self.x_hat))
    e = steering_angle - angle_steers_k
    self.x_hat = self.A.dot(self.x_hat) + self.B.dot(CS.steeringTorqueEps / torque_scale) + self.L.dot(e)

    if CS.vEgo < 0.3 or not active:
      lqr_log.active = False
      lqr_output = 0.
      self.reset()
    else:
      lqr_log.active = True

      # LQR
      u_lqr = float(self.angle_steers_des / self.dc_gain - self.K.dot(self.x_hat))
      lqr_output = torque_scale * u_lqr / self.scale

      # Integrator
      if CS.steeringPressed:
        self.i_lqr -= self.i_unwind_rate * float(np.sign(self.i_lqr))
      else:
        error = self.angle_steers_des - angle_steers_k
        i = self.i_lqr + self.ki * self.i_rate * error
        control = lqr_output + i

        if (error >= 0 and (control <= steers_max or i < 0.0)) or \
           (error <= 0 and (control >= -steers_max or i > 0.0)):
          self.i_lqr = i

      self.output_steer = lqr_output + self.i_lqr
      self.output_steer = clip(self.output_steer, -steers_max, steers_max)

      str2 = '/{} /{} /{} /{} /{} /{} /{} /{} /{} /{} /{} /{} /{}'.format(   
              v_ego_kph, steering_angle, self.angle_steers_des, angle_steers_k, torque_scale, log_scale, log_ki, log_dc_gain, u_lqr, lqr_output, self.i_lqr, steers_max, self.output_steer )
      self.trLQR.add( str2 ) 

    check_saturation = (CS.vEgo > 10) and not CS.steeringRateLimited and not CS.steeringPressed
    saturated = self._check_saturation(self.output_steer, check_saturation, steers_max)

    lqr_log.steerAngle = angle_steers_k + path_plan.angleOffset
    lqr_log.i = self.i_lqr
    lqr_log.output = self.output_steer
    lqr_log.lqrOutput = lqr_output
    lqr_log.saturated = saturated
    return self.output_steer, float(self.angle_steers_des), lqr_log
