import type { ExperienceMode } from './AnalysisDashboard';
import './ModeToggle.css';

/**
 * Beginner / Pro (§11, §12).
 *
 * Beginner is the default, and the toggle says what changes rather than being
 * a bare switch: a user should not have to click it to find out what they are
 * missing. The copy is explicit that Pro adds *detail*, not *truth* - nothing
 * safety-relevant is hidden from a beginner.
 *
 * Implemented as radio buttons rather than a checkbox or two styled divs, so
 * arrow keys move between them, the group has a name, and a screen reader
 * announces the selected mode without any ARIA of our own.
 */

export interface ModeToggleProps {
  readonly mode: ExperienceMode;
  readonly onChange: (mode: ExperienceMode) => void;
}

const OPTIONS: ReadonlyArray<{ value: ExperienceMode; label: string; hint: string }> = [
  { value: 'BEGINNER', label: 'Başlangıç', hint: 'Özet ve gerekçe' },
  { value: 'PRO', label: 'Profesyonel', hint: 'Ham değerler ve referanslar' },
];

export function ModeToggle({ mode, onChange }: ModeToggleProps) {
  return (
    <fieldset className="mode-toggle">
      <legend className="mode-toggle__legend">Görünüm</legend>
      {OPTIONS.map((option) => (
        <label key={option.value} className="mode-toggle__option">
          <input
            type="radio"
            name="experience-mode"
            value={option.value}
            checked={mode === option.value}
            onChange={() => onChange(option.value)}
          />
          <span className="mode-toggle__label">{option.label}</span>
          <span className="mode-toggle__hint">{option.hint}</span>
        </label>
      ))}
    </fieldset>
  );
}
