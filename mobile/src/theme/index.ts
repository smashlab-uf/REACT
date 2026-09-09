export const colors = {
  primary: '#0021A5',
  primaryMuted: '#e8eaf7',
  primaryBorder: '#b3bce8',
  accent: '#FA4616',
  accentMuted: '#fee6de',
  onPrimary: '#ffffff',
  textPrimary: '#111827',
  textSecondary: '#5b6472',
  textMuted: '#8a919c',
  border: '#dcdfe4',
  surface: '#ffffff',
  surfaceMuted: '#f5f6f8',
  disabled: '#c3c8d0',
  success: '#1f9d55',
  danger: '#c0392b',
  toastBackground: '#20242c',
  toastText: '#ffffff',
};

export const radius = {
  sm: 8,
  md: 12,
  lg: 16,
  pill: 999,
};

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
  xxl: 32,
};

export const typography = {
  screenTitle: { fontSize: 28, fontWeight: '700' as const, color: colors.textPrimary },
  heading: { fontSize: 24, fontWeight: '800' as const, color: colors.textPrimary },
  label: { fontSize: 16, fontWeight: '600' as const, color: colors.textPrimary },
  body: { fontSize: 15, color: colors.textSecondary },
  caption: { fontSize: 13, fontWeight: '600' as const, color: colors.textMuted },
};

export const buttonBase = {
  borderRadius: radius.md,
  paddingVertical: 16,
  alignItems: 'center' as const,
};
