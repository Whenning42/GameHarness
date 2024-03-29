# An class for predicting the current reward for an Art of Rally game running in the Harness.

import harness
import util
import rewards.model_lib as model_lib
import matplotlib.pyplot as plt
import numpy as np
import pathlib
import torch
import os
import gin
from profiler import Profiler

def _LogitsToBool(logits):
    return logits[1] > logits[0]

def _GetVel(speed, is_reverse):
    is_reverse = _LogitsToBool(is_reverse)
    if not speed.isnumeric():
        return None
    vel = int(speed)
    if is_reverse:
        vel *= -1
    return vel

@gin.configurable
def LinearReward(speed, is_reverse, is_penalized, penalty_value = 25, baseline_value = 0):
    if None in (speed, is_reverse, is_penalized):
        return None

    is_penalized = _LogitsToBool(is_penalized)

    vel = _GetVel(speed, is_reverse)
    if vel is None:
        return None

    penalty = 0
    if is_penalized:
        penalty = penalty_value

    return vel - penalty + baseline_value

@gin.configurable
# A shaped reward suitable for racing games. For v > k, this reward is proportional to -1/v
# i.e. the negative time it'd take to travel a fixed length of track. For v near 0, the
# function is instead proportional to v thus incentivizing an acceleration to a non-neglible
# speed, while not blowing up due to the -1/v term.
#
# TODO: Make function more parameterizable?
def TimeReward(speed, is_reverse, is_penalized, penalty_value = 1):
    if None in (speed, is_reverse, is_penalized):
        return None

    is_penalized = is_penalized[1] > is_penalized[0]

    vel = _GetVel(speed, is_reverse)
    if vel is None:
        return None

    # Put a diagram here?
    if vel < 0:
        p = -1
    elif vel < 5:
        p = 6.0 / 70 * vel - 1
    else:
        p = -4.0 / (vel + 2)

    # Transform the reward from [-1, 0] w/ mean of a weak policy at -.9 and a strong one
    # at -.2 to [-1.8, 1.2] w/ mean of weak policy at -1.5 and a strong one at .6
    p = 3*p + 1.2

    penalty = 0
    if is_penalized:
        penalty = penalty_value

    return p - penalty

@gin.configurable
def SteadyReward(speed, is_reverse, is_penalized, penalty_value = 1):
    if None in (speed, is_reverse, is_penalized):
        return None

    is_penalized = is_penalized[1] > is_penalized[0]

    vel = _GetVel(speed, is_reverse)
    if vel is None:
        return None

    if vel < 5:
        p = -1 + 2 * vel / 5
    else:
        p = 1

    penalty = 0
    if is_penalized:
        penalty = penalty_value

    return p - penalty

# 'plot_output'==True saves images showing the classification results of the model as well
# as the pixels the classifiers ran over for use in training/validation flows.
@gin.configurable
class ArtOfRallyReward():
    def __init__(self, plot_output = False, out_dir = None, device = "cuda:0", start_frame = 0, disable_speed_detection = False, reward_fn = TimeReward, gamma = .99, profiler=Profiler(no_op=True)):
        self.plot_output = plot_output
        self.out_dir = None
        if out_dir is not None:
            self.out_dir = os.path.join(out_dir, "aor_features_dbg")
        self.device = device
        self.frame = start_frame
        self.shaped_reward = reward_fn
        self.gamma = gamma
        self.profiler = profiler

        if plot_output:
            pathlib.Path(self.out_dir).mkdir(parents = True, exist_ok = True)

        # The capture methods are initialized in attach_to_harness().
        self.capture_detect_speed = None
        self.capture_is_reverse = None
        self.capture_is_penalized = None

        if disable_speed_detection:
            self.detect_speed_model = None
        else:
            self.detect_speed_model = model_lib.SpeedRecognitionSVM("models/svm.pkl")
        self.is_reverse_model = model_lib.BinaryClassifier("models/is_reverse_classifier.pth").to(device)
        self.is_reverse_model.eval()
        self.is_penalized_model = model_lib.BinaryClassifier("models/is_penalized_classifier.pth").to(device)
        self.is_penalized_model.eval()

    def _plot_reward(self, frame, features):
        label = ""
        plot_i = 1
        for feature in features.keys():
            image, prediction = features[feature]
            label += f"{feature}: {prediction}\n"
            if image is not None:
                plt.subplot(2, 2, plot_i)
                plt.imshow(image)
                plot_i += 1
                np.save(os.path.join(self.out_dir, f"{feature}_in_{frame:05d}.npy"), \
                        image)
                with open(os.path.join(self.out_dir, f"{feature}_pred_{frame:05d}.txt"), 'a') \
                        as f:
                    pred_item = prediction
                    f.write(f"{pred_item}\n")

        plt.figure(1)
        plt.suptitle(label)
        plt.savefig(os.path.join(self.out_dir, f"predicted_reward_{frame:05d}.png"))
        plt.clf()

    # Initializes the capture ROI methods using capture instances created in the harness.
    def attach_to_harness(self, harness):
        self.harness = harness

        self.capture_detect_speed = harness.add_capture(util.LoadJSON("configs/aor_rois.json")["detect_speed"]["roi"]["region"])
        self.capture_is_reverse = harness.add_capture(util.LoadJSON("configs/aor_rois.json")["is_reverse"]["roi"]["region"])
        self.capture_is_penalized = harness.add_capture(util.LoadJSON("configs/aor_rois.json")["is_penalized"]["roi"]["region"])

    def predict_detect_speed(self, detect_speed_roi):
        return self.detect_speed_model([detect_speed_roi])

    def predict_is_reverse(self, is_reverse_roi):
        is_reverse_x = is_reverse_roi
        is_reverse_x = self.is_reverse_model.ConvertToDomain(is_reverse_x)
        is_reverse_x = torch.unsqueeze(is_reverse_x, 0)
        is_reverse_x = is_reverse_x.to(self.device)
        return self.is_reverse_model(is_reverse_x)

    def predict_is_penalized(self, is_penalized_roi):
        is_penalized_x = is_penalized_roi
        is_penalized_x = self.is_penalized_model.ConvertToDomain(is_penalized_x)
        is_penalized_x = torch.unsqueeze(is_penalized_x, 0)
        is_penalized_x = is_penalized_x.to(self.device)
        return self.is_penalized_model(is_penalized_x)

    # Returns a dict of
    #   'train_reward', 'eval_reward', 'vel', 'is_penalized', 'is_reverse'
    def on_tick(self):
        self.profiler.begin("Color space conversion")
        detect_speed_roi = util.npBGRAtoRGB(self.capture_detect_speed())
        # Captured gives (w, h, c) w/ c == 4, BGRA
        is_reverse_roi = util.npBGRAtoRGB(self.capture_is_reverse())
        is_penalized_roi = util.npBGRAtoRGB(self.capture_is_penalized())
        self.profiler.end("Color space conversion")

        # Convert predicted list of single char strings into a string.
        self.profiler.begin("speed_rec")
        predicted_detect_speed = "".join(list(self.predict_detect_speed(detect_speed_roi)[0]))
        self.profiler.begin("is_reverse", end="speed_rec")
        predicted_is_reverse = self.predict_is_reverse(is_reverse_roi)[0]
        self.profiler.begin("is_penalized", end="is_reverse")
        predicted_is_penalized = self.predict_is_penalized(is_penalized_roi)[0]
        self.profiler.end("is_penalized")
        is_reverse = _LogitsToBool(predicted_is_reverse).item()
        is_penalized = _LogitsToBool(predicted_is_penalized).item()

        vel = _GetVel(predicted_detect_speed, predicted_is_reverse)
        if vel is None:
            vel = 0

        eval_reward = LinearReward(predicted_detect_speed, predicted_is_reverse, predicted_is_penalized)
        if eval_reward is None:
            eval_reward = -1

        shaped_reward = self.shaped_reward(predicted_detect_speed, predicted_is_reverse, predicted_is_penalized)
        if shaped_reward is None:
            shaped_reward = -1

        if self.plot_output:
            self._plot_reward(self.frame, {"detect_speed": [detect_speed_roi, predicted_detect_speed],
                                           "is_reverse": [is_reverse_roi, predicted_is_reverse],
                                           "is_penalized": [is_penalized_roi, predicted_is_penalized],
                                           "eval_reward": [None, eval_reward]})
        self.frame += 1

        out = {'train_reward': shaped_reward * (1-self.gamma),
                'eval_reward': eval_reward,
                'vel': vel,
                'is_penalized': is_penalized,
                'is_reverse': is_reverse}
        return out
