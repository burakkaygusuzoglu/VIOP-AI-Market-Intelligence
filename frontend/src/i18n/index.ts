import { tr, type Translations } from './tr';

export type Locale = 'tr';

const dictionaries: Record<Locale, Translations> = { tr };

export const DEFAULT_LOCALE: Locale = 'tr';

export function useTranslations(locale: Locale = DEFAULT_LOCALE): Translations {
  return dictionaries[locale];
}

export type { Translations };
