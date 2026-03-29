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
