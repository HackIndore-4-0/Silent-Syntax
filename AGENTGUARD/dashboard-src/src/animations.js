// Light, shared GSAP motion primitives — subtle entrance/stagger only.
// Kept deliberately small: this is a polish layer, not an animation system.
import gsap from 'gsap'

export const EASE = 'power2.out'

export function fadeInUp(target, opts = {}) {
  return gsap.from(target, {
    opacity: 0,
    y: 8,
    duration: 0.45,
    ease: EASE,
    ...opts,
  })
}

export function staggerInUp(targets, opts = {}) {
  return gsap.from(targets, {
    opacity: 0,
    y: 8,
    duration: 0.4,
    ease: EASE,
    stagger: 0.05,
    ...opts,
  })
}
