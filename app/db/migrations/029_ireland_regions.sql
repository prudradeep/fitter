DELETE r FROM regions r
JOIN countries c ON c.id = r.country_id
WHERE c.name = 'Ireland'
  AND r.name NOT IN ('Border', 'West', 'Mid-West', 'South-East', 'South-West', 'Dublin', 'Mid-East', 'Midland');

INSERT INTO regions (country_id, name)
SELECT c.id, region_rows.name
FROM countries c
JOIN (
  SELECT 'Border' AS name
  UNION ALL SELECT 'West'
  UNION ALL SELECT 'Mid-West'
  UNION ALL SELECT 'South-East'
  UNION ALL SELECT 'South-West'
  UNION ALL SELECT 'Dublin'
  UNION ALL SELECT 'Mid-East'
  UNION ALL SELECT 'Midland'
) region_rows
WHERE c.name = 'Ireland'
  AND NOT EXISTS (
    SELECT 1 FROM regions r
    WHERE r.country_id = c.id AND r.name = region_rows.name
  );
