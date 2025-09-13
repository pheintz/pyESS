import pygame
import time

DEVICE_INDEX = 0
HZ = 10  # Lower for easier reading
AXES = {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5}

pygame.init()
pygame.joystick.init()
if pygame.joystick.get_count() == 0:
    print("No joystick found.")
    exit(1)
js = pygame.joystick.Joystick(DEVICE_INDEX)
js.init()
print(f"Using [{DEVICE_INDEX}]: {js.get_name()}")
clock = pygame.time.Clock()

while True:
    for _ in pygame.event.get():
        pass
    # Read left stick, right stick, triggers, buttons, dpad
    lx = js.get_axis(AXES["LX"])
    ly = -js.get_axis(AXES["LY"])
    rx = js.get_axis(AXES["RX"])
    ry = -js.get_axis(AXES["RY"])
    lt = js.get_axis(AXES["LT"])
    rt = js.get_axis(AXES["RT"])
    buttons = [js.get_button(i) for i in range(js.get_numbuttons())]
    hat = js.get_hat(0) if js.get_numhats() > 0 else (0, 0)
    print(f"Left Stick: LX={lx:.3f}, LY={ly:.3f} | Right Stick: RX={rx:.3f}, RY={ry:.3f}")
    print(f"Triggers: LT={lt:.3f}, RT={rt:.3f}")
    print(f"Buttons: {buttons} | DPad: {hat}")
    print("---")
    clock.tick(HZ)