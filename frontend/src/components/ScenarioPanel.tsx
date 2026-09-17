import type {
  Contradiction,
  NarrativeSegment,
  ScenarioReadModel,
  SynthesisReadModel,
} from '../domain/models';
import { formatScore } from '../format/display';
import { ProvenanceBadge } from './ProvenanceBadge';
import './ScenarioPanel.css';

/**
 * Bull, bear, neutral, the Devil's Advocate and the contradictions (§29-§31).
 *
 * ## Three cases, never three shares of one
 *
 * The cases are shown side by side and nothing sums or normalises them. Each
 * carries the quality the Phase 4 engine scored for it independently, which is
 * why a bull case at 56 and a bear case at 21 do not add to anything: they are
 * two separate questions about the same evidence, not a split of one total.
 * The neutral case carries no score at all, because the model scores the
 * coherence of a *directional* case and inventing a neutral number would be
 * inventing a measurement.
 *
 * ## The challenge is mandatory and may be empty
 *
 * A Devil's Advocate section that says "the counter-evidence is limited" is a
 * real answer. Manufacturing an objection to fill the space would read like
 * analysis while being fiction, which is worse than an acknowledged absence.
 */

const CASE_LABEL = {
  BULL: 'Yükseliş senaryosu',
  BEAR: 'Düşüş senaryosu',
  NEUTRAL: 'Nötr senaryo',
} as const;

const STATE_LABEL: Record<string, string> = {
  UNAVAILABLE: 'Değerlendirilemedi',
  INACTIVE: 'Etkin değil',
  FORMING: 'Oluşuyor',
  WAITING_FOR_CONFIRMATION: 'Teyit bekleniyor',
  CONFIRMED: 'Teyitli',
  INVALIDATED: 'Geçersiz',
};

/**
 * Render model narrative.
 *
 * A `fact` segment is a value Python rendered from the deterministic registry,
 * so the number shown is the analysis's and not the model's. It is rendered as
 * a distinct element carrying its own provenance - an observation read off a
 * chart must not look like a calculated measurement (§17).
 *
 * Everything is plain React text. No `dangerouslySetInnerHTML`, so a payload
 * inside model prose stays inert.
 */
export function Narrative({ segments }: { segments: readonly NarrativeSegment[] }) {
  if (segments.length === 0) return null;
  return (
    <p className="narrative">
      {segments.map((segment, index) =>
        segment.kind === 'text' ? (
          <span key={index}>{segment.text}</span>
        ) : (
          <span
            key={index}
            className={`narrative__fact narrative__fact--${segment.kind}`}
            title={segment.label}
          >
            {segment.value}
            <ProvenanceBadge source={segment.source} />
          </span>
        ),
      )}
    </p>
  );
}

function ScenarioCard({
  scenario,
  narrative,
  showRaw,
}: {
  scenario: ScenarioReadModel;
  narrative: readonly NarrativeSegment[];
  showRaw: boolean;
}) {
  const tone = scenario.case.toLowerCase();
  return (
    <article className={`scenario scenario--${tone}`} aria-labelledby={`scenario-${tone}`}>
      <header className="scenario__header">
        <h4 className="scenario__heading" id={`scenario-${tone}`}>
          {CASE_LABEL[scenario.case]}
        </h4>
        <span className="scenario__state">{STATE_LABEL[scenario.state] ?? scenario.state}</span>
      </header>

      {scenario.quality ? (
        <p className="scenario__quality">
          <span className="scenario__score">
            {formatScore(scenario.quality.score, scenario.quality.outOf)}
          </span>
          <span className="scenario__kind">sezgisel puan · olasılık değildir</span>
        </p>
      ) : (
        <p className="scenario__quality scenario__quality--none">
          Bu senaryo için yönlü kalite puanı üretilmez.
        </p>
      )}

      <p className="scenario__reason">{scenario.reason}</p>
      <Narrative segments={narrative} />

      {scenario.supporting.length > 0 && (
        <div className="scenario__group">
          <h5 className="scenario__group-heading">Destekleyen ({scenario.supporting.length})</h5>
          <ul className="scenario__list">
            {scenario.supporting.slice(0, 6).map((item) => (
              <li key={item.id}>{item.reason}</li>
            ))}
          </ul>
        </div>
      )}

      {scenario.counter.length > 0 && (
        <div className="scenario__group">
          <h5 className="scenario__group-heading">Karşı ({scenario.counter.length})</h5>
          <ul className="scenario__list">
            {scenario.counter.slice(0, 6).map((item) => (
              <li key={item.id}>{item.reason}</li>
            ))}
          </ul>
        </div>
      )}

      {scenario.requirements.length > 0 && (
        <div className="scenario__group">
          <h5 className="scenario__group-heading">Bekleyen koşullar</h5>
          <ul className="scenario__list">
            {scenario.requirements.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {showRaw && scenario.components.length > 0 && (
        <div className="scenario__group">
          <h5 className="scenario__group-heading">Puan bileşenleri</h5>
          <ul className="scenario__components">
            {scenario.components.map((component) => (
              <li key={component.component}>
                <code>{component.component}</code>{' '}
                {component.awarded === null ? '—' : `${component.awarded}/${component.weight}`}{' '}
                <span className="scenario__availability">{component.availability}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </article>
  );
}

export interface ScenarioPanelProps {
  readonly scenarios: readonly ScenarioReadModel[];
  readonly contradictions: readonly Contradiction[];
  readonly synthesis?: SynthesisReadModel | undefined;
  readonly showRaw?: boolean;
}

export function ScenarioPanel({
  scenarios,
  contradictions,
  synthesis,
  showRaw = false,
}: ScenarioPanelProps) {
  if (scenarios.length === 0) return null;

  const narrativeFor = (item: ScenarioReadModel): readonly NarrativeSegment[] => {
    if (!synthesis || synthesis.status !== 'SUCCESS') return [];
    if (item.case === 'BULL') return synthesis.bullCase;
    if (item.case === 'BEAR') return synthesis.bearCase;
    return synthesis.neutralCase;
  };

  return (
    <section className="scenarios" aria-labelledby="scenarios-heading">
      <h3 className="scenarios__heading" id="scenarios-heading">
        Senaryolar
      </h3>
      <p className="scenarios__note">
        Üç senaryo bağımsızdır; birbirinin yüzdesi değildir ve toplamları bir anlam taşımaz.
      </p>

      <div className="scenarios__grid">
        {scenarios.map((item) => (
          <ScenarioCard
            key={item.case}
            scenario={item}
            narrative={narrativeFor(item)}
            showRaw={showRaw}
          />
        ))}
      </div>

      <section className="devils" aria-labelledby="devils-heading">
        <h4 className="devils__heading" id="devils-heading">
          Şeytanın avukatı
        </h4>
        {synthesis && synthesis.status === 'SUCCESS' && synthesis.devilsAdvocate.length > 0 ? (
          <Narrative segments={synthesis.devilsAdvocate} />
        ) : (
          <p className="devils__absent">
            Yapay zekâ sentezi bu analizde çalışmadığı için model tarafından yazılmış bir karşı
            görüş yok. Deterministik karşı kanıtlar yukarıdaki senaryolarda listelenir.
          </p>
        )}
      </section>

      {contradictions.length > 0 && (
        <section className="contradictions" aria-labelledby="contradictions-heading">
          <h4 className="contradictions__heading" id="contradictions-heading">
            Çelişkiler ({contradictions.length})
          </h4>
          {/* Never hidden and never averaged away. A disagreement between
              timeframes is the most useful thing on the page. */}
          <ul className="contradictions__list">
            {contradictions.map((item) => (
              <li key={item.id} className={`contradictions__item--${item.severity.toLowerCase()}`}>
                <span className="contradictions__type">{item.type}</span> {item.detail}
              </li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}
