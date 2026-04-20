SELECT id, name, agent_type, LEFT(COALESCE(api_key_hash, ' '), 20) AS key_prefix
FROM agents
ORDER BY name
LIMIT 40;
