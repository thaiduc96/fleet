import AppKit

/// The menu bar shown while Fleet has a window open. The Sửa menu is also what makes
/// ⌘C/⌘V/⌘X/⌘A/⌘Z reach text fields and the dashboard web view: AppKit routes those
/// key equivalents through the main menu, and an app without one gets none of them.
enum MainMenu {
    static func make(settingsTarget: AnyObject, openSettings: Selector) -> NSMenu {
        let main = NSMenu()
        main.addItem(submenu(fleetMenu(settingsTarget: settingsTarget, openSettings: openSettings)))
        main.addItem(submenu(editMenu()))
        return main
    }

    private static func fleetMenu(settingsTarget: AnyObject, openSettings: Selector) -> NSMenu {
        let menu = NSMenu(title: "Fleet")
        menu.addItem(withTitle: "Giới thiệu về Fleet",
                     action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Cài đặt…", action: openSettings, keyEquivalent: ",").target = settingsTarget
        menu.addItem(.separator())
        menu.addItem(withTitle: "Ẩn Fleet", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Thoát Fleet", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        return menu
    }

    private static func editMenu() -> NSMenu {
        let menu = NSMenu(title: "Sửa")
        menu.addItem(withTitle: "Hoàn tác", action: Selector(("undo:")), keyEquivalent: "z")
        menu.addItem(withTitle: "Làm lại", action: Selector(("redo:")), keyEquivalent: "Z")
        menu.addItem(.separator())
        menu.addItem(withTitle: "Cắt", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        menu.addItem(withTitle: "Sao chép", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        menu.addItem(withTitle: "Dán", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        menu.addItem(withTitle: "Chọn tất cả", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        return menu
    }

    private static func submenu(_ menu: NSMenu) -> NSMenuItem {
        let item = NSMenuItem(title: menu.title, action: nil, keyEquivalent: "")
        item.submenu = menu
        return item
    }
}
