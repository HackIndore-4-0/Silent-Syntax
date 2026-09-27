import { useTheme } from '../theme'
import badgeLight from './assets/brand-badge-light.png'
import badgeDark from './assets/brand-badge-dark.png'

// Real AgentGuard app-icon badge. Per brand guidance the light (white
// tile) badge is used on dark mode and the dark (navy tile) badge is
// used on light mode — each variant is the one with the stronger
// contrast against that theme's sidebar surface.
export function BrandMark({ size = 27 }) {
  const { theme } = useTheme()
  const src = theme === 'dark' ? badgeLight : badgeDark
  return <img src={src} width={size} height={size} alt="AgentGuard" draggable={false} />
}
