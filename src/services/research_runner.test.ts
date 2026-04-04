import { expect, test } from 'bun:test';

const researchRunnerModule = import('./research_runner');

test('buildSearchQueries generates complementary fact and forecast queries', async () => {
  const { buildSearchQueries } = await researchRunnerModule;

  const queries = buildSearchQueries(
    'Will the Oklahoma City Thunder win the NBA Western Conference Finals?',
    '',
  );

  expect(queries).toHaveLength(2);
  expect(queries[0]).toContain('latest news');
  expect(queries[0]).toContain('Oklahoma City Thunder');
  expect(queries[1]).toContain('prediction odds');
  expect(queries[1]).toContain('NBA Western Conference Finals');
});

test('deduplicateNewsResults keeps the first result for each unique URL', async () => {
  const { deduplicateNewsResults } = await researchRunnerModule;

  const deduplicated = deduplicateNewsResults([
    {
      title: 'One',
      url: 'https://example.com/a',
      snippet: 'A',
    },
    {
      title: 'Duplicate',
      url: 'https://example.com/a',
      snippet: 'B',
    },
    {
      title: 'Two',
      url: 'https://example.com/b',
      snippet: 'C',
    },
  ]);

  expect(deduplicated).toEqual([
    {
      title: 'One',
      url: 'https://example.com/a',
      snippet: 'A',
    },
    {
      title: 'Two',
      url: 'https://example.com/b',
      snippet: 'C',
    },
  ]);
});

test('parseResearchResponse supports catalyst and spillover fields', async () => {
  const { parseResearchResponse } = await researchRunnerModule;

  const parsed = parseResearchResponse(`{
    "summary": "Сильнее всего влияет форма команды и домашнее преимущество.",
    "key_facts": ["Thunder лидируют на Западе", "Состав без серьёзных травм"],
    "for_yes": ["Лучшая форма", "Домашнее преимущество"],
    "against_yes": ["Сильные соперники", "Риск травм"],
    "crowd_might_miss": "Глубина состава лучше, чем у конкурентов.",
    "catalyst_type": "sports_form",
    "primary_driver": "Текущая форма и здоровье лидеров Thunder",
    "second_order_effects": ["Подтверждение от betting odds", "Рост ожиданий по playoff seeding"],
    "disconfirming_signal": "Травма SGA перед плей-офф",
    "sources": ["https://example.com/nba"]
  }`);

  expect(parsed).not.toBeNull();
  expect(parsed?.catalyst_type).toBe('sports_form');
  expect(parsed?.primary_driver).toContain('Thunder');
  expect(parsed?.second_order_effects).toHaveLength(2);
  expect(parsed?.disconfirming_signal).toContain('Травма');
});

test('parseResearchResponse keeps working for legacy research briefs', async () => {
  const { parseResearchResponse } = await researchRunnerModule;

  const parsed = parseResearchResponse(`{
    "summary": "Короткая справка.",
    "key_facts": ["Факт 1"],
    "for_yes": ["Аргумент за"],
    "against_yes": ["Аргумент против"],
    "crowd_might_miss": "Толпа недооценивает календарь.",
    "sources": ["https://example.com/legacy"]
  }`);

  expect(parsed).not.toBeNull();
  expect(parsed?.catalyst_type).toBe('other');
  expect(parsed?.primary_driver).toBe('Нет данных.');
  expect(parsed?.second_order_effects).toEqual([]);
  expect(parsed?.disconfirming_signal).toBe('Нет данных.');
});
