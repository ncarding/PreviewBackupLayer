# encoding: utf-8

###########################################################################################################
#
#
# Reporter Plugin
#
# Read the docs:
# https://github.com/schriftgestalt/GlyphsSDK/tree/master/Python%20Templates/Reporter
#
#
###########################################################################################################


from __future__ import division, print_function, unicode_literals
from datetime import datetime
import re
import objc
from GlyphsApp import Glyphs
from GlyphsApp.plugins import ReporterPlugin
from AppKit import NSColor

class ReporterPreviewBackLayer(ReporterPlugin):

    @objc.python_method
    def settings(self):
        self.menuName = "Preview Backup Layer"
        self._last_debug_message = None
        self._last_active_layer_id = None
        self._debug_enabled = False
        self._last_scan_signature = None
        self._last_selected_layer_id = None

    @objc.python_method
    def start(self):
        self._debug("startup", "Plugin loaded and ready")

    @objc.python_method
    def _describe_layer(self, layer):
        if not layer:
            return "<None>"

        return (
            "name={name!r}, layerId={layer_id!r}, associatedMasterId={associated_master_id!r}, "
            "isMasterLayer={is_master}, isSpecialLayer={is_special}, visible={visible}"
        ).format(
            name=layer.name,
            layer_id=getattr(layer, "layerId", None),
            associated_master_id=getattr(layer, "associatedMasterId", None),
            is_master=getattr(layer, "isMasterLayer", None),
            is_special=getattr(layer, "isSpecialLayer", None),
            visible=getattr(layer, "visible", None),
        )

    @objc.python_method
    def _debug(self, title, details):
        if not self._debug_enabled:
            return
        message = "[{title}] {details}".format(title=title, details=details)
        if message == self._last_debug_message:
            return
        self._last_debug_message = message
        try:
            Glyphs.showMacroWindow()
        except Exception:
            pass
        print(message)

    @objc.python_method
    def _coerce_datetime(self, value):
        if value is None:
            return None

        if isinstance(value, datetime):
            return value

        value_str = str(value).strip()
        if not value_str:
            return None

        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
        ):
            try:
                return datetime.strptime(value_str, fmt)
            except Exception:
                pass

        return None

    @objc.python_method
    def _timestamp_from_name(self, name):
        if not name:
            return None

        m = re.search(r"(\d{4}[-/.]\d{2}[-/.]\d{2}[ T]\d{2}:\d{2}(?::\d{2})?)", str(name))
        if not m:
            return None

        return self._coerce_datetime(m.group(1).replace("T", " "))

    @objc.python_method
    def _is_backup_layer(self, layer):
        if not layer:
            return False

        name = (layer.name or "").lower()
        if "backup" in name:
            return True

        if getattr(layer, "isMasterLayer", False):
            return False

        # Exclude interpolation layers like {125, 100} and bracket layers like [100].
        if name.startswith("{") or name.startswith("["):
            return False

        # Ordinary duplicate/backup layers are typically non-master layers tied to a master.
        return bool(getattr(layer, "associatedMasterId", None))

    @objc.python_method
    def _layer_timestamp(self, layer):
        attrs = getattr(layer, "attributes", None) or {}
        for key in ("timestamp", "date", "creationDate", "backupTimestamp"):
            dt = self._coerce_datetime(attrs.get(key))
            if dt:
                return dt

        return self._timestamp_from_name(layer.name)

    @objc.python_method
    def _bezier_path_for_layer(self, layer):
        for attr_name in ("completeBezierPath", "bezierPath"):
            value = getattr(layer, attr_name, None)
            if value is None:
                continue
            try:
                return value() if callable(value) else value
            except Exception:
                continue
        return None

    @objc.python_method
    def _most_recent_backup_layer(self, glyph, active_layer):
        candidates = []
        debug_candidates = []
        for idx, candidate in enumerate(glyph.layers):
            if not candidate or candidate == active_layer:
                continue
            debug_candidates.append("#{idx}: {layer}".format(idx=idx, layer=self._describe_layer(candidate)))
            if not self._is_backup_layer(candidate):
                continue
            if getattr(candidate, "associatedMasterId", None) != getattr(active_layer, "layerId", None):
                continue

            dt = self._layer_timestamp(candidate)
            dt_key = dt.timestamp() if dt else float("-inf")
            candidates.append((dt_key, idx, candidate))

        scan_signature = (
            getattr(active_layer, "layerId", None),
            tuple(getattr(layer, "layerId", None) for _, _, layer in candidates),
            len(debug_candidates),
        )
        if scan_signature != self._last_scan_signature:
            self._last_scan_signature = scan_signature
            self._debug(
                "layer-scan",
                "active=({active}) candidates=[{candidates}]".format(
                    active=self._describe_layer(active_layer),
                    candidates=" | ".join(debug_candidates) if debug_candidates else "<none>",
                ),
            )

        if not candidates:
            self._debug("backup-selection", "No matching backup layer found for active layer")
            return None

        # Prefer explicit timestamp, then layer ordering as fallback.
        selected = max(candidates, key=lambda item: (item[0], item[1]))[2]
        selected_layer_id = getattr(selected, "layerId", None)
        if selected_layer_id != self._last_selected_layer_id:
            self._last_selected_layer_id = selected_layer_id
            self._debug("backup-selection", "Selected backup layer: {layer}".format(layer=self._describe_layer(selected)))
        return selected

    @objc.python_method
    def _line_width(self):
        current_tab = getattr(Glyphs.font, "currentTab", None)
        scale = getattr(current_tab, "scale", 1.0) if current_tab else 1.0
        if not scale:
            scale = 1.0
        return 2.0 / scale

    @objc.python_method
    def background(self, layer):
        try:
            active_layer_id = getattr(layer, "layerId", None)
            if active_layer_id != self._last_active_layer_id:
                self._last_active_layer_id = active_layer_id
                self._debug("background-entry", "Entered background() for {layer}".format(layer=self._describe_layer(layer)))

            glyph = layer.parent
            backup_layer = self._most_recent_backup_layer(glyph, layer)
            if not backup_layer:
                return

            bezier = self._bezier_path_for_layer(backup_layer)
            if not bezier:
                self._debug("drawing", "Backup layer had no bezier path: {layer}".format(layer=self._describe_layer(backup_layer)))
                return

            bezier = bezier.copy()
            bezier.setLineWidth_(self._line_width())

            fill_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.55, 0.85, 0.10)
            stroke_color = NSColor.colorWithCalibratedRed_green_blue_alpha_(0.02, 0.35, 0.75, 0.85)

            fill_color.set()
            bezier.fill()
            stroke_color.set()
            bezier.stroke()

            open_bezier = getattr(backup_layer, "completeOpenBezierPath", None) or getattr(backup_layer, "openBezierPath", None)
            if open_bezier:
                try:
                    open_bezier = open_bezier() if callable(open_bezier) else open_bezier
                    open_bezier = open_bezier.copy()
                    open_bezier.setLineWidth_(self._line_width())
                    stroke_color.set()
                    open_bezier.stroke()
                except Exception:
                    pass
        except Exception as error:
            print("Preview Backup Layer error:", error)

    @objc.python_method
    def __file__(self):
        """Please leave this method unchanged"""
        return __file__
