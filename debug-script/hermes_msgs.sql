SELECT id, agent_id, status, created_at, delivered_at, LEFT(content, 80) AS content_snippet
FROM gateway_messages
WHERE agent_id = '663ef262-f6f9-470f-b6fb-00f217d059a1'
ORDER BY created_at DESC
LIMIT 20;
