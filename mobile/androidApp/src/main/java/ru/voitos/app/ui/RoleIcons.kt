package ru.voitos.app.ui

import androidx.annotation.DrawableRes
import ru.voitos.app.R
import ru.voitos.app.model.ExecutorRole

@DrawableRes
fun roleIconRes(role: ExecutorRole): Int = roleIconRes(role.name, role.code, role.isEquipment)

@DrawableRes
fun roleIconRes(name: String, code: String = "", isEquipment: Boolean = false): Int {
    val s = "$name $code".lowercase()
    return when {
        isEquipment ||
            "трактор" in s || "экскават" in s || "погруз" in s ||
            "техник" in s || "машин" in s || "кран" in s -> R.drawable.ic_role_equipment
        "электр" in s || "свет" in s || "розет" in s -> R.drawable.ic_role_electric
        "сантех" in s || "труб" in s || "вода" in s || "смесител" in s -> R.drawable.ic_role_plumbing
        "маник" in s || "ногт" in s || "красот" in s || "парик" in s ||
            "космет" in s -> R.drawable.ic_role_beauty
        "убор" in s || "клининг" in s || "чист" in s -> R.drawable.ic_role_clean
        "снег" in s || "дворник" in s || "мороз" in s -> R.drawable.ic_role_snow
        "компьют" in s || "ноут" in s || "it" in s || "ай ти" in s ||
            "програм" in s -> R.drawable.ic_role_computer
        else -> R.drawable.ic_role_tools
    }
}
