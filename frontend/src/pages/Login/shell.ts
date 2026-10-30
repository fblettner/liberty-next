// The sign-in screens' frame (the login, the reset by mail, the change a temporary password needs).
import styled from '@emotion/styled'
import { colors, fontSize, fonts, radius, glass } from '../../theme'

export const Center = styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
`

export const Panel = styled.form`
  width: 340px;
  background: var(--ghost-bg, ${colors.bg.card});
  border: 1px solid ${colors.border};
  border-radius: ${radius.lg};
  padding: 24px;
  ${glass.surface}
  ${glass.specularSm}
`

export const Brand = styled.div`
  font-size: ${fontSize['3xl']};
  font-weight: 700;
  letter-spacing: -0.3px;
  margin-bottom: 4px;
`
export const Accent = styled.span`color: ${colors.blue.main};`
export const Tagline = styled.div`
  font-size: ${fontSize.base};
  color: ${colors.text.muted};
  margin-bottom: 18px;
`
export const Hint = styled.div`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
  line-height: 1.5;
  code { font-family: ${fonts.mono}; }
`

// A text link inside the panel ("Forgot password?", "Back to sign in").
export const LinkButton = styled.button`
  align-self: flex-start; background: none; border: none; padding: 0; cursor: pointer;
  color: ${colors.blue.main}; font-size: ${fontSize.sm}; font-family: inherit;
  &:hover { text-decoration: underline; }
`
