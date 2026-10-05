// Kestrel Explorer drop focus: after a drag from Kestrel ends in another app's window, Kestrel calls
// ActivateAtPointer (on org.gnome.Shell, at /com/regulusarms/KestrelFocus) and the window under the pointer gets the
// focus. On Wayland only the shell can do that; kes-setup --default installs and enables this extension.
import Meta from 'gi://Meta';
import Gio from 'gi://Gio';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="com.regulusarms.KestrelFocus">
    <method name="ActivateAtPointer">
      <arg type="b" name="activated" direction="out"/>
    </method>
  </interface>
</node>`;

const TYPES = [Meta.WindowType.NORMAL, Meta.WindowType.DIALOG, Meta.WindowType.MODAL_DIALOG, Meta.WindowType.UTILITY];

export default class KestrelFocus extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/com/regulusarms/KestrelFocus');
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
    }

    ActivateAtPointer() {
        // the topmost window on this workspace under the pointer
        const [x, y] = global.get_pointer();
        const workspace = global.workspace_manager.get_active_workspace();
        const actors = global.get_window_actors();   // bottom to top
        for (let i = actors.length - 1; i >= 0; i--) {
            const win = actors[i].get_meta_window();
            if (!win || win.minimized || !TYPES.includes(win.get_window_type()) || !win.located_on_workspace(workspace))
                continue;
            const r = win.get_frame_rect();
            if (x >= r.x && x < r.x + r.width && y >= r.y && y < r.y + r.height) {
                if (!win.has_focus())
                    Main.activateWindow(win);
                return true;
            }
        }
        return false;
    }
}
