# Records a human demo of Art of Rally gameplay.
#
# The hard-coded `DISCRETE` constant controls whether
# the demo is in keyboard or controller mode (True and
# False respectively).

import rewards.env_rally
import random
import evdev
from src.keyboard.controller import Controller
import time

# Build with:
# $ cd src/keyboard
# $ make
# $ cp src/keyboard/UserKeyboard/* ./
# Note: Virtualenv might get the compiled so name's python version wrong.
# If so, this needs to be manually fixed.
import UserKeyboard

DISCRETE = False

# Create environment
env = rewards.env_rally.ArtOfRallyEnv(out_dir = "analog_full", run_rate = 1.0, pause_rate = .1, is_demo = True)

p_conf = {"steps_between": int(3.5 * 8),
          "max_duration": 6,
          "space": env.action_space}

class Perturb:
    def __init__(self, conf):
        self.t = 0
        self.conf = conf
        self.cycle_len = self.conf["steps_between"]
        self.duration = -1
        self.action = None

    def step(self):
        self.t += 1
        cycle_t = self.t % self.cycle_len
        if cycle_t == 0:
            self.duration = random.randint(0, self.conf["max_duration"])
            self.action = env.action_space.sample()
            return self.action
        elif cycle_t < self.duration:
            return self.action
        else:
            return None

def GetAction(controller):
    s = controller.state()
    return (s["ls_x"], s["lt"], s["rt"])

def main():
    p = Perturb(p_conf)
    cont = env.controller
    kb = UserKeyboard.UserKeyboard()
    env.reset()

    was_perturbed = False
    while True:
        # U,   D,   L,   R
        # 111, 116, 113, 114
        action = p.step()
        perturbed = action is not None
        if DISCRETE:
            if perturbed:
                kb.disable()
                to_log = None
            else:
                if was_perturbed:
                    env.harness.keyboards[0].set_held_keys(set())
                kb.enable()
                to_log = [0, 0]
                state = kb.key_state()
                if state[111] and not state[116]:
                    to_log[0] = 1
                elif state[116] and not state[111]:
                    to_log[0] = 2
                if state[113] and not state[114]:
                    to_log[1] = 1
                elif state[114] and not state[113]:
                    to_log[1] = 2
            _, _, done, _ = env.step(action, logged_action=to_log, perturbed=perturbed)
        else:
            if perturbed and not cont.paused:
                print("Perturbed ", time.time())
                cont.apply_action(action)
                cont.lock_user()
            else:
                # On first step after perturbation, apply controller state
                print("Not perturbed ", time.time())
                if was_perturbed == False:
                    cont.apply_action(GetAction(cont))
                cont.unlock_user()
            _, _, done, _ = env.step(perturbed=perturbed)
        was_perturbed = perturbed

        if done:
            kb.disable()
            env.reset()
            kb.enable()

if __name__ == "__main__":
    main()
