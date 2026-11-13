"""Every module that registers background-job handlers, imported in one place.

The worker imports this module so all handlers exist before it claims a job; tests that
run jobs import it too. Adding a module with @jobs.handler(...) means adding it here.
"""

import app.modules.allocations.jobs  # noqa: F401  allocations.expire_reservations
import app.modules.announcements.router  # noqa: F401  announcements.fanout
import app.modules.complaints.jobs  # noqa: F401  complaints.auto_close
import app.modules.finance.jobs  # noqa: F401  fees.charge_run, fees.late_fees
import app.modules.identity.emails  # noqa: F401  email.org_registration, .activation, .password_reset_code, .otp
import app.modules.notifications.service  # noqa: F401  email.notification
import app.modules.registration.emails  # noqa: F401  email.student_request_* (D74)
from app.core import jobs as _jobs  # noqa: F401  maintenance.purge, maintenance.reap_stuck
