package ru.voitos.app.nav

/**
 * Maps server notification.type / deep_link → in-app route.
 * Keep in sync with docs/mobile/push-deeplinks.md
 */
object DeepLinks {
    private val WR = Regex("""voitos://app/work-requests/(\d+)(?:/(confirm|slots|rate|survey))?""")
    private val COL = Regex("""voitos://app/collections/(\d+)""")
    private val VOL = Regex("""voitos://app/volunteer/(\d+)""")

    sealed class Route {
        data object Home : Route()
        data object Subscription : Route()
        data object Profile : Route()
        data class WorkRequest(val id: Int, val action: String? = null) : Route()
        data class Collection(val id: Int) : Route()
        data class Volunteer(val askId: Int) : Route()
        data class Raw(val link: String) : Route()
    }

    fun parse(deepLink: String): Route {
        val link = deepLink.trim()
        if (link.isEmpty()) return Route.Home
        when {
            link.contains("/subscription") -> return Route.Subscription
            link.contains("/profile") -> return Route.Profile
            link.contains("/home") -> return Route.Home
        }
        WR.matchEntire(link)?.let { m ->
            return Route.WorkRequest(m.groupValues[1].toInt(), m.groupValues.getOrNull(2)?.ifBlank { null })
        }
        COL.matchEntire(link)?.let { m ->
            return Route.Collection(m.groupValues[1].toInt())
        }
        VOL.matchEntire(link)?.let { m ->
            return Route.Volunteer(m.groupValues[1].toInt())
        }
        return Route.Raw(link)
    }

    fun routeForType(type: String, entityId: Int?): Route = when (type) {
        "work_request.assigned",
        "work_request.no_executor",
        "work_request.executor_declined" ->
            if (entityId != null) Route.WorkRequest(entityId) else Route.Home
        "work_request.confirm_amount" ->
            if (entityId != null) Route.WorkRequest(entityId, "confirm") else Route.Home
        "work_request.slots_ready" ->
            if (entityId != null) Route.WorkRequest(entityId, "slots") else Route.Home
        "collection.offered",
        "collection.remind_3d",
        "collection.remind_1d",
        "collection.remind_2h",
        "collection.progress",
        "collection.closed" ->
            if (entityId != null) Route.Collection(entityId) else Route.Home
        "subscription.receipt_approved",
        "subscription.receipt_rejected",
        "subscription.renewal_4d",
        "subscription.renewal_2d",
        "subscription.renewal_2h",
        "subscription.onboarding_reward" -> Route.Subscription
        else -> Route.Home
    }
}
