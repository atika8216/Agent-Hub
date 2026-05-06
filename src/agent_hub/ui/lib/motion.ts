import type { Transition, Variants } from "motion/react";

/*
 * iOS-grade motion primitives shared across the app.
 *
 * The constants here are the single source of truth for any
 * Framer-Motion-driven animation. Keeping them in lockstep with the
 * CSS tokens declared in ``styles/globals.css`` (``--ease-ios``,
 * ``--ease-ios-emphasize``, ``--duration-page``) means a designer
 * tweaking the curves never has to chase JS values around the codebase.
 *
 * Variants are written as plain objects so they degrade well under
 * ``useReducedMotion()`` -- ``motion`` automatically zeros out
 * transforms when reduce-motion is on, leaving the opacity crossfade
 * intact.
 */

/* Shared tween for crossfades / non-spring entrances. 0.26s ≈
   --duration-page; the ease tuple matches --ease-ios verbatim. */
export const iosTween: Transition = {
  duration: 0.26,
  ease: [0.25, 0.1, 0.25, 1],
};

/* Critically-damped spring tuned for chat bubbles and the active nav
   pill. ``stiffness: 380`` keeps it brisk; ``damping: 32`` removes the
   characteristic Material overshoot so it lands the way iOS lands. */
export const iosSpring: Transition = {
  type: "spring",
  stiffness: 380,
  damping: 32,
  mass: 0.9,
};

/*
 * Route push/pop variants for ``<AnimatePresence>``. ``forward`` mirrors
 * the iOS UINavigationController affordance: pushing a screen slides
 * the new view in from the right; popping back slides the leaving view
 * out to the right. The trailing exit is shorter so the inbound page
 * "wins" perceptually -- this prevents the empty-frame flash you see
 * with symmetric durations.
 */
export const pageVariants = (forward: boolean): Variants => ({
  initial: { opacity: 0, x: forward ? 24 : -24 },
  animate: { opacity: 1, x: 0, transition: iosTween },
  exit: {
    opacity: 0,
    x: forward ? -16 : 16,
    transition: { ...iosTween, duration: 0.2 },
  },
});

/*
 * Chat bubble entrance. The slight upward translate + scale-from-98
 * gives messages a settled, sender-anchored feel without bouncing.
 * Used by both user (right-aligned) and assistant (left-aligned)
 * bubbles -- the spring physics carry the visual weight either way.
 */
export const bubbleVariants: Variants = {
  initial: { opacity: 0, y: 8, scale: 0.98 },
  animate: { opacity: 1, y: 0, scale: 1, transition: iosSpring },
  exit: { opacity: 0, y: -4, transition: iosTween },
};
