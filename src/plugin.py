# -*- coding: utf-8 -*-
from Plugins.Plugin import PluginDescriptor
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from Components.ActionMap import ActionMap
from Components.ConfigList import ConfigListScreen
from Components.Label import Label
from Components.ScrollLabel import ScrollLabel
from Components.config import (
    config,
    ConfigSubsection,
    ConfigSelection,
    ConfigText,
    ConfigYesNo,
    getConfigListEntry,
)
from Components.Language import language
from twisted.internet import reactor

import os
import threading
import time
from datetime import datetime

from .automapper import AutoMapper
from .epgcore import (
    EPGInjector,
    EPGParser,
    atomic_write_json,
    clone_sat_to_iptv,
    download_file,
    ensure_temp_dir,
    load_json,
    log_message,
    scan_bouquet_services,
    update_plugin_package,
    validate_xmltv_file,
)

PLUGIN_VERSION = "1.0.0"
PLUGIN_TITLE = "IPTV EPG Manager"
TEMP_XML_PATH = "/tmp/iptvepgmgr/guide.xml.gz"
TEMP_XML_FALLBACK = "/tmp/iptvepgmgr/guide.xml"
GLOBAL_WORKER = None
AUTOUPDATE_STARTED = False
ACTIVE_SESSION = None

# Format URL EPG dla panelu XUI.ONE:
# http://TWOJ_SERWER/xmltv.php  (bez loginu/hasła - publiczny endpoint panelu)
# lub http://TWOJ_SERWER:PORT/xmltv.php
# Użytkownik konfiguruje adres serwera w polu "Serwer XUI.ONE"
# a plugin automatycznie buduje pełny URL EPG.

SOURCE_DEFINITIONS = [
    {
        "id": "XUIONE",
        "label": "** XUI.ONE - Moja lista IPTV (zalecane) **",
        "urls": [],
        "xuione": True,
    },
    {
        "id": "AUTO_AIO",
        "label": "AUTO - AIO Polska + fallback międzynarodowy",
        "urls": [
            "https://epg.ovh/pl.gz",
            "https://epg.ovh/pltv.gz",
            "https://epgshare01.online/epgshare01/epg_ripper_PL1.xml.gz",
            "https://epg.pw/xmltv/epg_lite.xml.gz",
            "https://epg.pw/xmltv/epg.xml.gz",
        ],
    },
    {
        "id": "AUTO_PL_FAST",
        "label": "AUTO - Polska szybkie (OVH -> EPGShare)",
        "urls": [
            "https://epg.ovh/pl.gz",
            "https://epgshare01.online/epgshare01/epg_ripper_PL1.xml.gz",
            "https://epg.pw/xmltv/epg_lite.xml.gz",
        ],
    },
    {
        "id": "AUTO_PL_RICH",
        "label": "AUTO - Polska rozbudowane (OVH rich -> arch -> Share)",
        "urls": [
            "https://epg.ovh/pltv.gz",
            "https://epg.ovh/plar.gz",
            "https://epgshare01.online/epgshare01/epg_ripper_PL1.xml.gz",
            "https://epg.pw/xmltv/epg.xml.gz",
        ],
    },
    {
        "id": "EPGOVH_STD",
        "label": "PL - EPG.OVH standard",
        "urls": ["https://epg.ovh/pl.gz"],
    },
    {
        "id": "EPGOVH_RICH",
        "label": "PL - EPG.OVH rich",
        "urls": ["https://epg.ovh/pltv.gz"],
    },
    {
        "id": "EPGSHARE_PL1",
        "label": "PL - EPGShare PL1",
        "urls": ["https://epgshare01.online/epgshare01/epg_ripper_PL1.xml.gz"],
    },
    {
        "id": "EPGPW_LITE",
        "label": "GLOBAL - EPG.PW lite",
        "urls": ["https://epg.pw/xmltv/epg_lite.xml.gz"],
    },
    {
        "id": "EPGPW_ALL",
        "label": "GLOBAL - EPG.PW full",
        "urls": ["https://epg.pw/xmltv/epg.xml.gz"],
    },
    {
        "id": "EPGSHARE_UK1",
        "label": "UK - EPGShare UK1",
        "urls": ["https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz"],
    },
    {
        "id": "EPGSHARE_DE1",
        "label": "DE - EPGShare DE1",
        "urls": ["https://epgshare01.online/epgshare01/epg_ripper_DE1.xml.gz"],
    },
    {
        "id": "EPGSHARE_FR1",
        "label": "FR - EPGShare FR1",
        "urls": ["https://epgshare01.online/epgshare01/epg_ripper_FR1.xml.gz"],
    },
    {
        "id": "EPGSHARE_IT1",
        "label": "IT - EPGShare IT1",
        "urls": ["https://epgshare01.online/epgshare01/epg_ripper_IT1.xml.gz"],
    },
    {
        "id": "EPGSHARE_CZ1",
        "label": "CZ - EPGShare CZ1",
        "urls": ["https://epgshare01.online/epgshare01/epg_ripper_CZ1.xml.gz"],
    },
    {
        "id": "EPGSHARE_SK1",
        "label": "SK - EPGShare SK1",
        "urls": ["https://epgshare01.online/epgshare01/epg_ripper_SK1.xml.gz"],
    },
    {
        "id": "CUSTOM",
        "label": "--- Własny URL ---",
        "urls": [],
    },
]
SOURCE_CHOICES = [(item["id"], item["label"]) for item in SOURCE_DEFINITIONS]
SOURCE_MAP = dict([(item["id"], item) for item in SOURCE_DEFINITIONS])


def get_lang():
    try:
        code = language.getLanguage() or ""
        return "pl" if "pl" in code.lower() else "en"
    except Exception:
        return "en"


LANG = get_lang()

TR = {
    "header": {
        "pl": "%s v%s" % (PLUGIN_TITLE, PLUGIN_VERSION),
        "en": "%s v%s" % (PLUGIN_TITLE, PLUGIN_VERSION),
    },
    "author_details": {
        "pl": "Wtyczka EPG dla listy IPTV | xui.one",
        "en": "EPG plugin for IPTV list | xui.one",
    },
    "build_details": {
        "pl": "Wersja: %s | Python3 | Silnik AIO + klon SAT" % PLUGIN_VERSION,
        "en": "Version: %s | Python3 | AIO engine + SAT clone" % PLUGIN_VERSION,
    },
    "help_arrows": {
        "pl": "< Zmień źródło strzałkami Lewo/Prawo >",
        "en": "< Change source using Left/Right arrows >",
    },
    "source_label": {"pl": "Wybierz źródło EPG:", "en": "Select EPG source:"},
    "xuione_label": {"pl": "   >> Adres serwera XUI.ONE (np. http://serwer.pl:8080):", "en": "   >> XUI.ONE server address (e.g. http://server.com:8080):"},
    "custom_label": {"pl": "   >> Własny URL XML/XML.GZ:", "en": "   >> Custom XML/XML.GZ URL:"},
    "map_file_label": {"pl": "Plik cache mapowania:", "en": "Mapping cache file:"},
    "autoupdate_label": {"pl": "Auto-import co 24h:", "en": "Auto-import every 24h:"},
    "import_days_label": {"pl": "Zakres importu (dni):", "en": "Import range (days):"},
    "btn_hide": {"pl": "Ukryj w tle", "en": "Hide"},
    "btn_import": {"pl": "Importuj EPG", "en": "Import EPG"},
    "btn_map": {"pl": "Mapuj kanały", "en": "Map channels"},
    "btn_update": {"pl": "Aktualizuj wtyczkę", "en": "Update plugin"},
    "status_ready": {
        "pl": "Gotowy. Wybierz źródło EPG i naciśnij [Importuj EPG].\nDla listy IPTV z xui.one wybierz 'XUI.ONE' i podaj adres serwera.\n",
        "en": "Ready. Select EPG source and press [Import EPG].\nFor IPTV list from xui.one select 'XUI.ONE' and enter server address.\n",
    },
    "hidden_msg": {
        "pl": "Wtyczka działa w tle. Po zakończeniu pokażę komunikat.",
        "en": "Plugin is running in background. A notification will be shown when finished.",
    },
    "busy": {
        "pl": "Zadanie już trwa. Poczekaj na zakończenie poprzedniego importu.",
        "en": "A job is already running. Please wait until it finishes.",
    },
    "scan_start": {
        "pl": "Analiza bukietów i kanałów live...",
        "en": "Scanning bouquets and live channels...",
    },
    "scan_done": {
        "pl": "Live IPTV: {iptv} | SAT: {sat} | Pominięto VOD/serie: {skipped}",
        "en": "Live IPTV: {iptv} | SAT: {sat} | Skipped VOD/series: {skipped}",
    },
    "download_try": {"pl": "Próba źródła {}/{}", "en": "Trying source {}/{}"},
    "download_ok": {"pl": "Źródło działa: {}", "en": "Source works: {}"},
    "download_fail": {"pl": "Nie udało się pobrać żadnego źródła EPG.", "en": "Unable to download any EPG source."},
    "xuione_no_server": {
        "pl": "Brak adresu serwera XUI.ONE. Wpisz adres w polu 'Adres serwera XUI.ONE'.",
        "en": "XUI.ONE server address is empty. Enter the server address in 'XUI.ONE server address' field.",
    },
    "clone_start": {"pl": "Kopiowanie EPG z kanałów SAT do IPTV...", "en": "Cloning SAT EPG to IPTV..."},
    "clone_done": {"pl": "SAT sklonowano dla kanałów: {}", "en": "SAT cloned for channels: {}"},
    "mapping_start": {"pl": "Budowanie mapowania XMLTV...", "en": "Building XMLTV mapping..."},
    "mapping_cache": {"pl": "Używam zapisanego cache mapowania.", "en": "Using saved mapping cache."},
    "mapping_saved": {"pl": "Zapisano mapowanie. Grupy: {groups} | XML: {xml}", "en": "Mapping saved. Groups: {groups} | XML: {xml}"},
    "mapping_empty": {"pl": "Nie znaleziono dopasowań XMLTV dla kanałów live.", "en": "No XMLTV matches found for live channels."},
    "import_start": {"pl": "Import XMLTV do pamięci EPG...", "en": "Importing XMLTV into EPG cache..."},
    "import_done": {
        "pl": "ZAKOŃCZONO!\nKanały SAT: {sat}\nKanały XML: {xml_channels}\nZdarzenia XML: {xml_events}",
        "en": "DONE!\nSAT channels: {sat}\nXML channels: {xml_channels}\nXML events: {xml_events}",
    },
    "cache_cleared": {"pl": "Cache i pliki tymczasowe zostały wyczyszczone.", "en": "Cache and temporary files were cleared."},
    "update_start": {"pl": "Aktualizacja wtyczki...", "en": "Updating plugin..."},
    "update_ok": {"pl": "Aktualizacja zakończona. Zrestartuj GUI Enigmy.", "en": "Update finished. Restart Enigma GUI."},
    "update_fail": {"pl": "Aktualizacja nie powiodła się: {}", "en": "Update failed: {}"},
    "fatal": {"pl": "Błąd krytyczny: {}", "en": "Fatal error: {}"},
    "background_done": {"pl": "Auto-import EPG zakończony.", "en": "Automatic EPG import finished."},
    "nothing_to_do": {"pl": "Nie znaleziono kanałów IPTV live do przetworzenia.", "en": "No live IPTV channels found."},
    "mapping_only_done": {"pl": "Mapowanie gotowe. XML ID: {}", "en": "Mapping ready. XML IDs: {}"},
}


def _(key):
    return TR.get(key, {}).get(LANG, TR.get(key, {}).get("en", key))


config.plugins.IPTVEPGManager = ConfigSubsection()
config.plugins.IPTVEPGManager.source_select = ConfigSelection(default="XUIONE", choices=SOURCE_CHOICES)
config.plugins.IPTVEPGManager.xuione_server = ConfigText(default="http://", fixed_size=False, visible_width=80)
config.plugins.IPTVEPGManager.custom_url = ConfigText(default="https://", fixed_size=False, visible_width=80)
config.plugins.IPTVEPGManager.mapping_file = ConfigText(default="/etc/enigma2/iptv_epg_mapping.json", fixed_size=False)
config.plugins.IPTVEPGManager.auto_update = ConfigYesNo(default=True)
config.plugins.IPTVEPGManager.import_days = ConfigSelection(default="3", choices=[("2", "2"), ("3", "3"), ("5", "5"), ("7", "7")])
config.plugins.IPTVEPGManager.last_update = ConfigText(default="0", fixed_size=False)
config.plugins.IPTVEPGManager.last_source = ConfigText(default="", fixed_size=False)


def save_plugin_config():
    try:
        config.plugins.IPTVEPGManager.save()
    except Exception:
        pass


def _build_xuione_urls(server_base):
    """Buduje listę możliwych URL EPG dla serwera XUI.ONE."""
    base = (server_base or "").rstrip("/")
    if not base or base in ("http://", "https://"):
        return []
    # XUI.ONE / Xtream Codes EPG endpoints
    return [
        base + "/epg.xml",
        base + "/epg.xml.gz",
        base + "/xmltv.php",
        base + "/epg/epg.xml",
    ]


class EPGWorker(object):
    def __init__(self):
        self._lock = threading.Lock()
        self.running = False

    def _selected_sources(self):
        source_id = config.plugins.IPTVEPGManager.source_select.value
        if source_id == "XUIONE":
            server = (config.plugins.IPTVEPGManager.xuione_server.value or "").strip()
            urls = _build_xuione_urls(server)
            return [("XUIONE", url) for url in urls] if urls else []
        if source_id == "CUSTOM":
            url = (config.plugins.IPTVEPGManager.custom_url.value or "").strip()
            return [("CUSTOM", url)] if url and url.startswith(("http://", "https://")) else []
        source = SOURCE_MAP.get(source_id, SOURCE_MAP["AUTO_AIO"])
        out = []
        for url in source.get("urls", []):
            out.append((source_id, url))
        return out

    def _temp_target_for_url(self, url):
        ensure_temp_dir()
        lower = (url or "").lower()
        return TEMP_XML_PATH if lower.endswith(".gz") else TEMP_XML_FALLBACK

    def _download_selected_source(self, log_cb):
        sources = self._selected_sources()
        if not sources:
            source_id = config.plugins.IPTVEPGManager.source_select.value
            if source_id == "XUIONE":
                if log_cb:
                    log_cb(_("xuione_no_server"))
            return None, None, None
        total = len(sources)
        for idx, (source_key, url) in enumerate(sources):
            if log_cb:
                log_cb(_("download_try").format(idx + 1, total) + ": %s" % url)
            target = self._temp_target_for_url(url)
            if download_file(url, target, timeout=180, retries=2, log_cb=log_cb) and validate_xmltv_file(target):
                config.plugins.IPTVEPGManager.last_source.value = url
                save_plugin_config()
                if log_cb:
                    log_cb(_("download_ok").format(url))
                return target, source_key, url
        return None, None, None

    def _load_mapping_cache(self, mapping_path, services_signature, source_key):
        payload = load_json(mapping_path)
        if not isinstance(payload, dict):
            return None
        meta = payload.get("_meta", {}) if isinstance(payload.get("_meta"), dict) else {}
        mapping = payload.get("mapping") if isinstance(payload.get("mapping"), dict) else None
        if not mapping:
            if payload and "_meta" not in payload:
                return payload
            return None
        if meta.get("services_signature") != services_signature:
            return None
        if meta.get("source_key") != source_key:
            return None
        return mapping

    def _save_mapping_cache(self, mapping_path, mapping, services_signature, source_key, stats):
        payload = {
            "_meta": {
                "plugin_version": PLUGIN_VERSION,
                "services_signature": services_signature,
                "source_key": source_key,
                "saved_at": int(time.time()),
                "groups": int(stats.get("groups", 0)),
                "xml_ids": int(stats.get("xml_ids", 0)),
            },
            "mapping": mapping,
        }
        atomic_write_json(payload, mapping_path)

    def clear_cache(self):
        mapping_path = config.plugins.IPTVEPGManager.mapping_file.value
        for path in [mapping_path, TEMP_XML_PATH, TEMP_XML_FALLBACK, "/tmp/iptvepgmgr/mapping_debug.json"]:
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    def run_update(self, callback_log=None):
        if self.running:
            if callback_log:
                callback_log(_("busy"))
            return False
        if not self._lock.acquire(False):
            if callback_log:
                callback_log(_("busy"))
            return False
        self.running = True
        try:
            ok, message = update_plugin_package(log_cb=callback_log)
            if ok:
                if callback_log:
                    callback_log(_("update_ok"))
                return True
            if callback_log:
                callback_log(_("update_fail").format(message))
            return False
        finally:
            self.running = False
            try:
                self._lock.release()
            except Exception:
                pass

    def run_mapping_only(self, callback_log=None):
        if self.running:
            if callback_log:
                callback_log(_("busy"))
            return False
        if not self._lock.acquire(False):
            if callback_log:
                callback_log(_("busy"))
            return False
        self.running = True
        try:
            return self._run_mapping_core(callback_log)
        finally:
            self.running = False
            try:
                self._lock.release()
            except Exception:
                pass

    def _run_mapping_core(self, callback_log):
        if callback_log:
            callback_log(_("scan_start"))
        scan = scan_bouquet_services()
        if callback_log:
            callback_log(_("scan_done").format(iptv=len(scan.get("iptv", [])), sat=len(scan.get("sat_services", [])), skipped=scan.get("skipped", 0)))
        services = scan.get("iptv", [])
        if not services:
            if callback_log:
                callback_log(_("nothing_to_do"))
            return False
        xml_path, source_key, _url = self._download_selected_source(callback_log)
        if not xml_path:
            if callback_log:
                callback_log(_("download_fail"))
            return False
        if callback_log:
            callback_log(_("mapping_start"))
        mapper = AutoMapper(log_callback=callback_log)
        mapping, stats = mapper.generate_mapping(services, xml_path)
        if not mapping:
            if callback_log:
                callback_log(_("mapping_empty"))
            return False
        self._save_mapping_cache(config.plugins.IPTVEPGManager.mapping_file.value, mapping, scan.get("services_signature", ""), source_key, stats)
        if callback_log:
            callback_log(_("mapping_saved").format(groups=stats.get("groups", 0), xml=stats.get("xml_ids", 0)))
            callback_log(_("mapping_only_done").format(len(mapping)))
        return True

    def run_import(self, callback_log=None, silent=False):
        if self.running:
            if callback_log:
                callback_log(_("busy"))
            return False
        if not self._lock.acquire(False):
            if callback_log:
                callback_log(_("busy"))
            return False
        self.running = True
        try:
            return self._run_import_core(callback_log=callback_log, silent=silent)
        finally:
            self.running = False
            try:
                self._lock.release()
            except Exception:
                pass

    def _run_import_core(self, callback_log=None, silent=False):
        if callback_log:
            callback_log(_("scan_start"))
        scan = scan_bouquet_services()
        iptv_services = scan.get("iptv", [])
        sat_services = scan.get("sat_services", [])
        skipped = scan.get("skipped", 0)
        services_signature = scan.get("services_signature", "")
        if callback_log:
            callback_log(_("scan_done").format(iptv=len(iptv_services), sat=len(sat_services), skipped=skipped))
        if not iptv_services:
            if callback_log:
                callback_log(_("nothing_to_do"))
            return False

        xml_path, source_key, _url = self._download_selected_source(callback_log)
        if not xml_path:
            if callback_log:
                callback_log(_("download_fail"))
            return False

        import_days = int(config.plugins.IPTVEPGManager.import_days.value or "3")
        injector = EPGInjector()

        if callback_log:
            callback_log(_("clone_start"))
        clone_stats = clone_sat_to_iptv(injector, iptv_services, sat_services, days_ahead=import_days, log_cb=callback_log)
        cloned_refs = clone_stats.get("injected_refs", set())
        if callback_log:
            callback_log(_("clone_done").format(clone_stats.get("channels", 0)))

        remaining_services = [service for service in iptv_services if service.get("full_ref") not in cloned_refs]
        mapping_path = config.plugins.IPTVEPGManager.mapping_file.value
        mapping = self._load_mapping_cache(mapping_path, services_signature, source_key)

        if mapping:
            if callback_log:
                callback_log(_("mapping_cache"))
        else:
            if callback_log:
                callback_log(_("mapping_start"))
            mapper = AutoMapper(log_callback=callback_log)
            mapping, stats = mapper.generate_mapping(remaining_services, xml_path)
            if mapping:
                self._save_mapping_cache(mapping_path, mapping, services_signature, source_key, stats)
                if callback_log:
                    callback_log(_("mapping_saved").format(groups=stats.get("groups", 0), xml=stats.get("xml_ids", 0)))

        if not mapping:
            config.plugins.IPTVEPGManager.last_update.value = str(int(time.time()))
            save_plugin_config()
            if callback_log:
                callback_log(_("mapping_empty"))
                callback_log(_("import_done").format(sat=clone_stats.get("channels", 0), xml_channels=0, xml_events=0))
            return True

        if callback_log:
            callback_log(_("import_start"))
        parser = EPGParser(xml_path)
        xml_channels = set()
        xml_events = 0
        imported_refs = set(cloned_refs)
        buffer_counter = 0

        def parser_progress(message):
            if callback_log:
                callback_log(message)

        for service_ref, payload, channel_id in parser.iter_events(mapping, days_ahead=import_days, progress_cb=parser_progress):
            injector.add_event(service_ref, payload)
            xml_channels.add(channel_id)
            imported_refs.add(service_ref)
            xml_events += 1
            buffer_counter += 1
            if buffer_counter >= 4000:
                injector.commit()
                buffer_counter = 0

        injector.commit()
        config.plugins.IPTVEPGManager.last_update.value = str(int(time.time()))
        save_plugin_config()
        if callback_log:
            callback_log(_("import_done").format(
                sat=clone_stats.get("channels", 0),
                xml_channels=len(xml_channels),
                xml_events=xml_events,
            ))
        if not silent and ACTIVE_SESSION:
            try:
                reactor.callFromThread(
                    ACTIVE_SESSION.open,
                    MessageBox,
                    _("background_done"),
                    MessageBox.TYPE_INFO,
                    timeout=10,
                )
            except Exception:
                pass
        return True


class IPTV_EPG_Config(Screen, ConfigListScreen):
    skin = """
        <screen name="IPTV_EPG_Config" position="center,center" size="900,600" title="IPTV EPG Manager">
            <widget name="header_title" position="10,5" size="880,30" font="Regular;22" halign="center" valign="center" foregroundColor="#00FFD700"/>
            <widget name="author_info" position="10,38" size="880,22" font="Regular;16" halign="center" foregroundColor="#00AAAAAA"/>
            <widget name="last_info" position="10,62" size="880,20" font="Regular;14" halign="center" foregroundColor="#00888888"/>
            <widget name="help_arrows" position="10,85" size="880,20" font="Regular;14" halign="center" foregroundColor="#0088CCFF"/>
            <widget name="config" position="10,110" size="880,160" scrollbarMode="showOnDemand" font="Regular;18"/>
            <widget name="label_status" position="10,275" size="880,22" font="Regular;16" foregroundColor="#00AAAAAA"/>
            <widget name="status" position="10,300" size="880,250" font="Regular;15" foregroundColor="#00FFFFFF"/>
            <widget name="key_red" position="10,560" size="200,32" font="Regular;16" halign="center" backgroundColor="#009F1313" foregroundColor="#00FFFFFF"/>
            <widget name="key_green" position="230,560" size="200,32" font="Regular;16" halign="center" backgroundColor="#00136B13" foregroundColor="#00FFFFFF"/>
            <widget name="key_yellow" position="450,560" size="200,32" font="Regular;16" halign="center" backgroundColor="#00A08000" foregroundColor="#00FFFFFF"/>
            <widget name="key_blue" position="670,560" size="200,32" font="Regular;16" halign="center" backgroundColor="#00131379" foregroundColor="#00FFFFFF"/>
        </screen>
    """

    def __init__(self, session):
        Screen.__init__(self, session)
        global ACTIVE_SESSION
        ACTIVE_SESSION = session

        self.worker = GLOBAL_WORKER
        self["author_info"] = Label(_("author_details"))
        self["last_info"] = Label(self._build_last_info())
        self["header_title"] = Label(_("header"))
        self["help_arrows"] = Label(_("help_arrows"))
        self["key_red"] = Label(_("btn_hide"))
        self["key_green"] = Label(_("btn_import"))
        self["key_yellow"] = Label(_("btn_map"))
        self["key_blue"] = Label(_("btn_update"))
        self["label_status"] = Label("Log:")
        self["status"] = ScrollLabel(_("status_ready"))

        self.list = []
        self.createConfigList()
        ConfigListScreen.__init__(self, self.list)

        self["actions"] = ActionMap(["SetupActions", "ColorActions", "DirectionActions", "MenuActions"], {
            "red": self.minimize_window,
            "green": self.start_import_gui,
            "yellow": self.start_mapping_gui,
            "blue": self.update_plugin_gui,
            "menu": self.clear_cache_gui,
            "cancel": self.close,
            "save": self.start_import_gui,
            "left": self.keyLeft,
            "right": self.keyRight,
        }, -1)

    def _build_last_info(self):
        try:
            last_update = int(config.plugins.IPTVEPGManager.last_update.value or "0")
        except Exception:
            last_update = 0
        line = _("build_details")
        if last_update <= 0:
            return "%s | Ostatni import: brak" % line
        return "%s | Ostatni import: %s" % (line, datetime.fromtimestamp(last_update).strftime("%d.%m.%Y %H:%M"))

    def createConfigList(self):
        self.list = [
            getConfigListEntry(_("source_label"), config.plugins.IPTVEPGManager.source_select),
        ]
        if config.plugins.IPTVEPGManager.source_select.value == "XUIONE":
            self.list.append(getConfigListEntry(_("xuione_label"), config.plugins.IPTVEPGManager.xuione_server))
        elif config.plugins.IPTVEPGManager.source_select.value == "CUSTOM":
            self.list.append(getConfigListEntry(_("custom_label"), config.plugins.IPTVEPGManager.custom_url))
        self.list.append(getConfigListEntry(_("map_file_label"), config.plugins.IPTVEPGManager.mapping_file))
        self.list.append(getConfigListEntry(_("autoupdate_label"), config.plugins.IPTVEPGManager.auto_update))
        self.list.append(getConfigListEntry(_("import_days_label"), config.plugins.IPTVEPGManager.import_days))

    def updateConfigList(self):
        self.createConfigList()
        self["config"].setList(self.list)

    def keyLeft(self):
        ConfigListScreen.keyLeft(self)
        self.updateConfigList()

    def keyRight(self):
        ConfigListScreen.keyRight(self)
        self.updateConfigList()

    def minimize_window(self):
        self.hide()
        self.session.open(MessageBox, _("hidden_msg"), MessageBox.TYPE_INFO, timeout=5)

    def gui_update_log(self, message):
        try:
            stamp = datetime.now().strftime("%H:%M:%S")
            old = self["status"].getText()
            self["status"].setText(old + "[%s] %s\n" % (stamp, message))
            self["status"].lastPage()
            self["last_info"].setText(self._build_last_info())
        except Exception:
            pass

    def log(self, message):
        reactor.callFromThread(self.gui_update_log, str(message))

    def save_settings(self):
        for item in self["config"].list:
            try:
                item[1].save()
            except Exception:
                pass
        save_plugin_config()

    def start_import_gui(self):
        self.save_settings()
        if self.worker.running:
            self.gui_update_log(_("busy"))
            return
        self["status"].setText(_("status_ready"))

        def runner():
            try:
                ok = self.worker.run_import(callback_log=self.log, silent=False)
                if not ok:
                    reactor.callFromThread(self.gui_update_log, _("fatal").format("import stopped"))
            except Exception as error:
                reactor.callFromThread(self.gui_update_log, _("fatal").format(error))
                log_message("Import exception: %s" % error)

        threading.Thread(target=runner, daemon=True).start()

    def start_mapping_gui(self):
        self.save_settings()
        if self.worker.running:
            self.gui_update_log(_("busy"))
            return
        self["status"].setText(_("status_ready"))

        def runner():
            try:
                ok = self.worker.run_mapping_only(callback_log=self.log)
                if not ok:
                    reactor.callFromThread(self.gui_update_log, _("fatal").format("mapping stopped"))
            except Exception as error:
                reactor.callFromThread(self.gui_update_log, _("fatal").format(error))
                log_message("Mapping exception: %s" % error)

        threading.Thread(target=runner, daemon=True).start()

    def update_plugin_gui(self):
        if self.worker.running:
            self.gui_update_log(_("busy"))
            return
        self.gui_update_log(_("update_start"))

        def runner():
            try:
                ok = self.worker.run_update(callback_log=self.log)
                if not ok:
                    reactor.callFromThread(self.gui_update_log, _("update_fail").format("opkg"))
            except Exception as error:
                reactor.callFromThread(self.gui_update_log, _("update_fail").format(error))

        threading.Thread(target=runner, daemon=True).start()

    def clear_cache_gui(self):
        self.worker.clear_cache()
        self.gui_update_log(_("cache_cleared"))


def AutoUpdateCheck():
    if config.plugins.IPTVEPGManager.auto_update.value and GLOBAL_WORKER is not None:
        try:
            last_update = int(config.plugins.IPTVEPGManager.last_update.value or "0")
        except Exception:
            last_update = 0
        if int(time.time()) - last_update > 86400 and not GLOBAL_WORKER.running:
            log_message("[AutoUpdate] Starting background import")
            threading.Thread(target=GLOBAL_WORKER.run_import, kwargs={"silent": True}, daemon=True).start()
    reactor.callLater(3600, AutoUpdateCheck)


def StartSession(**kwargs):
    global AUTOUPDATE_STARTED
    if not AUTOUPDATE_STARTED:
        AUTOUPDATE_STARTED = True
        reactor.callLater(60, AutoUpdateCheck)


def main(session, **kwargs):
    session.open(IPTV_EPG_Config)


def Plugins(**kwargs):
    return [
        PluginDescriptor(name=PLUGIN_TITLE, description="IPTV EPG importer z obsługą XUI.ONE", where=PluginDescriptor.WHERE_PLUGINMENU, icon="plugin.png", fnc=main),
        PluginDescriptor(where=PluginDescriptor.WHERE_SESSIONSTART, fnc=StartSession),
    ]


GLOBAL_WORKER = EPGWorker()
