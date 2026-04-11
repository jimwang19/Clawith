SELECT name, is_enabled, type, created_at 
FROM agent_triggers 
WHERE agent_id::text LIKE 'e6b32063%' 
ORDER BY created_at DESC 
LIMIT 8;
