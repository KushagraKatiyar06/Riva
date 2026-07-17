// Cloudflare Worker that handles RAG queries. Embeds the question using the
// AI binding, searches Vectorize, and calls Gemini to generate an answer.
// Currently unused - the backend calls Cloudflare directly instead.

export interface Env {
  AI: Ai;
  VECTORIZE: VectorizeIndex;
  GEMINI_API_KEY: string;
}

function corsHeaders(origin: string) {
  return {
    'Access-Control-Allow-Origin': origin || '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = request.headers.get('Origin') || '*';

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    const url = new URL(request.url);

    if (url.pathname === '/health') {
      return new Response(JSON.stringify({ ok: true }), {
        headers: { 'Content-Type': 'application/json' },
      });
    }

    if (url.pathname === '/chat' && request.method === 'POST') {
      try {
        const body = await request.json<{ query: string; domains?: string[] }>();
        const { query, domains } = body;

        if (!query?.trim()) {
          return new Response(JSON.stringify({ error: 'query is required' }), {
            status: 400,
            headers: { 'Content-Type': 'application/json', ...corsHeaders(origin) },
          });
        }

        const embedResult = await env.AI.run('@cf/baai/bge-base-en-v1.5', {
          text: [query],
        }) as { data: number[][] };
        const vector = embedResult.data[0];

        const queryResult = await env.VECTORIZE.query(vector, {
          topK: 20,
          returnMetadata: 'all',
        });

        let matches = queryResult.matches;

        if (domains?.length) {
          const domainSet = new Set(domains);
          matches = matches.filter(m => domainSet.has((m.metadata as Record<string, string>)?.domain));
        }
        matches = matches.slice(0, 15);

        if (matches.length === 0) {
          return new Response(
            JSON.stringify({ answer: 'No relevant data found. Have the agents finished extracting?' }),
            { headers: { 'Content-Type': 'application/json', ...corsHeaders(origin) } }
          );
        }

        const grouped: Record<string, string[]> = {};
        for (const m of matches) {
          const meta = m.metadata as Record<string, string>;
          const domain = meta?.domain || 'unknown';
          const text = meta?.text || '';
          if (!grouped[domain]) grouped[domain] = [];
          grouped[domain].push(text);
        }

        const context = Object.entries(grouped)
          .map(([d, chunks]) => `### ${d}\n${chunks.join('\n')}`)
          .join('\n\n');

        const prompt =
          'You are a competitive intelligence analyst. Use ONLY the context below to answer.\n' +
          'Be specific. Where multiple domains are present, highlight differences clearly.\n\n' +
          `---\n${context}\n---\n\nQuestion: ${query}\nAnswer:`;

        const geminiRes = await fetch(
          `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=${env.GEMINI_API_KEY}`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ contents: [{ parts: [{ text: prompt }] }] }),
          }
        );

        const geminiData = await geminiRes.json() as {
          candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>;
        };
        const answer =
          geminiData.candidates?.[0]?.content?.parts?.[0]?.text?.trim() ||
          'Unable to generate an answer.';

        const sources = [...new Set(
          matches.map(m => (m.metadata as Record<string, string>)?.url).filter(Boolean)
        )];

        return new Response(
          JSON.stringify({ answer, sources }),
          { headers: { 'Content-Type': 'application/json', ...corsHeaders(origin) } }
        );
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        return new Response(JSON.stringify({ error: message }), {
          status: 500,
          headers: { 'Content-Type': 'application/json', ...corsHeaders(origin) },
        });
      }
    }

    return new Response('Not found', { status: 404 });
  },
};
