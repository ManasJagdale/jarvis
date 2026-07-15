"""
orb.py

Renders Jarvis's ambient "presence" orb -- a soft glowing sphere, similar
in spirit to Siri/assistant-style orbs. Built with Pillow because plain
Tkinter/CustomTkinter drawing primitives can't do soft radial gradients
or blur; Pillow renders it pixel-by-pixel as a transparent RGBA image
that Tkinter can then just display like any other picture.

Design choice -- pre-render, don't recompute:
    Every animation frame is rendered ONCE at startup (a slow "breathing"
    loop: gentle size + brightness pulse, a few seconds per cycle) rather
    than recomputed live. Cycling pre-rendered frames with a Tkinter
    `after()` timer is nearly free; recomputing a Gaussian blur 10+ times
    a second is not, and would visibly stutter.

    This keeps the orb a lightweight ambient touch (per design choice --
    "nice touch, mostly static/lightly animated"), not a reactive/live
    element. If you later want it to pulse faster while Jarvis is
    thinking, swap the fixed FRAME_DELAY_MS in gui.py for two speeds
    rather than re-rendering here.

Usage:
    from orb import generate_orb_frames
    frames = generate_orb_frames()   # list[PIL.Image.Image], RGBA
"""

import math

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter

ORB_SIZE = 180  # canvas size (px, square) each frame is rendered at
RADIUS_FRAC = 0.62  # sphere radius as a fraction of ORB_SIZE / 2
FRAME_COUNT = 40  # frames in one full breathing cycle
FRAME_DELAY_MS = 90  # ms between frames -> ~3.6s per full cycle

# Sphere palette -- deep blue base with a bright cyan->purple diagonal
# swirl band, matching the reference aesthetic (dark navy sphere, glowing
# cyan-to-violet streak across the middle).
_DARK_BLUE = np.array([6, 18, 70])
_DEEP_BLUE = np.array([12, 45, 140])
_CYAN = np.array([130, 235, 225])
_PURPLE = np.array([120, 70, 225])


def _render_base_sphere(size: int) -> Image.Image:
    """One high-quality render of the static sphere, no glow yet."""
    center = size / 2
    radius = size * RADIUS_FRAC / 2

    yy, xx = np.mgrid[0:size, 0:size]
    dx, dy = xx - center, yy - center
    dist = np.sqrt(dx**2 + dy**2)
    mask = dist <= radius

    # Diagonal coordinate system for the bright swirl band.
    u = (dx * 0.7 + dy * 0.7) / radius
    v = (-dx * 0.7 + dy * 0.7) / radius
    t = np.clip((u + 1) / 2, 0, 1)
    band = np.exp(-(v**2) / 0.12)

    img = np.zeros((size, size, 3))
    for c in range(3):
        base = _DARK_BLUE[c] * (1 - t) + _DEEP_BLUE[c] * t
        swirl = _CYAN[c] * band + _PURPLE[c] * (band * t)
        img[:, :, c] = base + swirl

    # Spherical shading: dim toward the rim.
    falloff = np.clip(1 - (dist / radius) ** 2, 0, 1) ** 0.5
    img = img * (0.55 + 0.45 * falloff[:, :, None])
    img = np.clip(img, 0, 255).astype(np.uint8)

    alpha = (mask * 255).astype(np.uint8)
    rgba = np.dstack([img, alpha])
    return Image.fromarray(rgba, "RGBA")


def generate_orb_frames(size: int = ORB_SIZE, count: int = FRAME_COUNT) -> list[Image.Image]:
    """
    Return `count` RGBA frames forming one seamless breathing loop.

    The sphere grows and brightens slightly on a sine curve (not a linear
    ramp-and-reset), so frame `count - 1` flows back into frame 0 without
    a visible jump when cycled on a timer.
    """
    base = _render_base_sphere(size)
    frames = []

    for i in range(count):
        phase = (i / count) * 2 * math.pi
        pulse = (math.sin(phase) + 1) / 2  # eases 0 -> 1 -> 0

        scale = 1.0 + 0.035 * pulse
        brightness = 1.0 + 0.12 * pulse

        scaled_size = max(1, int(size * scale))
        scaled = base.resize((scaled_size, scaled_size), Image.LANCZOS)
        scaled = ImageEnhance.Brightness(scaled).enhance(brightness)

        sphere_layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        offset = (size - scaled_size) // 2
        sphere_layer.paste(scaled, (offset, offset), scaled)

        # Soft outer glow: a blurred, dimmer copy composited behind the
        # sphere. Glow strength tracks the same pulse for a subtle
        # "breathing" halo.
        glow = sphere_layer.filter(ImageFilter.GaussianBlur(size * 0.09))
        glow_alpha = glow.split()[3].point(lambda a: int(a * (0.5 + 0.3 * pulse)))
        glow.putalpha(glow_alpha)

        composed = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        composed = Image.alpha_composite(composed, glow)
        composed = Image.alpha_composite(composed, sphere_layer)
        frames.append(composed)

    return frames
