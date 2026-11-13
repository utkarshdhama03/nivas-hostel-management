-- =============================================================================
-- 0015_notification_links (12 November 2026, D84): every notification opens a page that exists.
--
--   * The 'allocation' category goes: its notices linked to the student's room page, removed by D79
--     (no code has written it since). Any such rows are deleted.
--   * A link must be one of the pages listed below, or NULL (no link). The application keeps the
--     same list in app/modules/notifications/models.py (NOTIFICATION_LINKS); the frontend checks
--     each one is a real route (src/components/notification-links.test.ts).
--   * Announcements sent to "everyone" (or to staff) had no link; they now open the reader's own
--     Announcements page: /portal/announcements for a student, /admin/announcements for staff.
--   * Any other link that isn't on the list is set to NULL: the message stays, the dead link goes.
--
-- Rows are changed per organization: RLS is FORCEd, even for the owner running this.
-- =============================================================================

DO $$
DECLARE org record;
BEGIN
  FOR org IN SELECT id FROM organizations ORDER BY id LOOP
    PERFORM set_config('app.org_id', org.id::text, true);
    DELETE FROM notifications WHERE category = 'allocation';
    UPDATE notifications n
       SET link = CASE u.kind WHEN 'student' THEN '/portal/announcements' ELSE '/admin/announcements' END
      FROM users u
     WHERE u.id = n.user_id AND n.category = 'announcements' AND n.link IS NULL;
    UPDATE notifications SET link = NULL
     WHERE link IS NOT NULL AND link NOT IN (
       '/portal/fees', '/portal/leave', '/portal/complaints', '/portal/announcements',
       '/admin/leave', '/admin/complaints', '/admin/registration-requests', '/admin/announcements');
  END LOOP;
  PERFORM set_config('app.org_id', '', true);
END $$;

ALTER TABLE notifications DROP CONSTRAINT notifications_category_check;
ALTER TABLE notifications ADD CONSTRAINT notifications_category_check CHECK (category IN
  ('security','fees','leave','complaints','announcements','admission','reminders'));

ALTER TABLE notifications ADD CONSTRAINT notifications_link_known CHECK (link IS NULL OR link IN (
  '/portal/fees', '/portal/leave', '/portal/complaints', '/portal/announcements',
  '/admin/leave', '/admin/complaints', '/admin/registration-requests', '/admin/announcements'));
