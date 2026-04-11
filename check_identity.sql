-- Check OrgMember DingTalk identity for jim's user
SELECT om.id, om.external_id, om.unionid, om.name, ip.provider_type, ip.name as provider_name
FROM org_members om
LEFT JOIN identity_providers ip ON om.provider_id = ip.id
WHERE om.user_id = '28e0128a-2fd3-472a-ac29-6602afd64e94';
