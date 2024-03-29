# current items only
select count(s.name) "Total Scenes", SUM(case when s.status in ('complete', 'unavailable') then 1 else 0 end) "Complete",  SUM(case when s.status not in ('complete', 'unavailable') then 1 else 0 end) "Processing",   u.email, u.first_name, u.last_name from ordering_scene s, ordering_order o, auth_user u where s.order_id = o.id and o.user_id = u.id and s.status != 'purged' group by u.email, u.first_name, u.last_name order by "Total Scenes" DESC;

# all time items
select count(s.name) "Total Scenes", SUM(case when s.status in ('purged', 'complete', 'unavailable') then 1 else 0 end) "Complete",  SUM(case when s.status not in ('purged', 'complete', 'unavailable') then 1 else 0 end) "Processing",   u.email, u.first_name, u.last_name from ordering_scene s, ordering_order o, auth_user u where s.order_id = o.id and o.user_id = u.id   group by u.email, u.first_name, u.last_name order by "Total Scenes" DESC;
