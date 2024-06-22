import argparse
import math
import threading
import time

import numpy as np

from bounce_rl.environments.noita import noita_env


def run_env(instance: int):
    env = noita_env.NoitaEnv(
        instance=instance,
        x_pos=instance,
        run_config={"run_rate": 1, "use_x_proxy": xproxy},
        seed=args.seed,
    )

    i = 0
    while True:
        if not step:
            time.sleep(10)
            continue

        a_disc, a_cont = env.action_space.sample()
        a = i * 8 * (-1) ** instance / 180 * math.pi
        x, y = 0.3 * math.cos(a), 0.3 * math.sin(a)
        a_cont = np.array([x, y])
        ret = env.step((a_disc, a_cont))
        i += 1

        if ret is not None:
            pixels, reward, done, info = ret
            print(
                f"mean_pixels: {np.mean(pixels)}, r: {reward}, "
                f"done: {done}, info {info}"
            )
        else:
            done = True
        if done:
            env.reset()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--manual_control", type=bool, default=False)
    args = parser.parse_args()

    xproxy = True
    step = True
    if args.manual_control:
        xproxy = False
        step = False

    noita_env.NoitaEnv.pre_init(num_envs=2)
    env_t1 = threading.Thread(target=run_env, args=(0,))
    env_t2 = threading.Thread(target=run_env, args=(1,))
    env_t1.start()
    env_t2.start()
    env_t1.join()
    env_t2.join()
