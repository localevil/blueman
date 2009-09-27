# Copyright (C) 2008 Valmantas Paliksa <walmis at balticum-tv dot lt>
# Copyright (C) 2008 Tadas Dailyda <tadas at dailyda dot com>
#
# Licensed under the GNU General Public License Version 3
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
# 
from blueman.Functions import *
from blueman.plugins.AppletPlugin import AppletPlugin
from blueman.bluez.Device import Device as BluezDevice
from blueman.main.Device import Device
from blueman.gui.Notification import Notification
from blueman.main.NetConf import have
from blueman.main.PulseAudioUtils import PulseAudioUtils
from subprocess import Popen, PIPE
import gobject

import dbus
from blueman.main.SignalTracker import SignalTracker

class SourceRedirector:
	def __init__(self, module_id, device_path, pa_utils):
		self.module_id = module_id
		self.pa_utils = pa_utils
		self.device = Device(device_path)
		self.signals = SignalTracker()
		self.signals.Handle("dbus", dbus.SystemBus(), self.on_source_prop_change, "PropertyChanged", "org.bluez.AudioSource", path=device_path)
		
		self.pacat = None
		self.parec = None
		
		dprint("Starting source redirector")
		def sources_cb(sources):
			for k, v in sources.iteritems():
				props = v["proplist"]
				if "bluetooth.protocol" in props:
					if props["bluetooth.protocol"] == "a2dp_source":
						if v["owner_module"] == self.module_id:
							dprint("Found source", k)
							self.start_redirect(k)
							return
		
		self.pa_utils.ListSources(sources_cb)
		
	def start_redirect(self, source):
		self.parec = Popen(["parec", "-d", str(source)], stdout=PIPE)
		self.pacat = Popen(["pacat", "--client-name=Blueman", "--stream-name=%s" % self.device.Address, "--property=application.icon_name=blueman"], stdin=self.parec.stdout)
		
	def on_source_prop_change(self, key, value):
		if key == "State":
			if value == "disconnected":
				if self.pacat:
					self.pacat.terminate()
				if self.parec:
					self.parec.terminate()
				self.signals.DisconnectAll()
				self.pa_utils.UnloadModule(self.module_id, lambda x: dprint(x))

class PulseAudio(AppletPlugin):
	__author__ = "Walmis"
	__description__ = _("Automatically loads pulseaudio bluetooth module after audio device is connected.\n<b>Note:</b> Requires pulseaudio 0.9.14 or higher")
	__icon__ = "audio-card"
	__options__  = {
		"checked" : (bool, False)
	}
	def on_load(self, applet):
		self.signals = SignalTracker()
		if not self.get_option("checked"):
			self.set_option("checked", True)
			if not have("pactl"):
				applet.Plugins.SetConfig("PulseAudio", False)
				return
			
		self.bus = dbus.SystemBus()
		
		self.connected_sources = []
		
		self.signals.Handle("dbus", self.bus, self.on_sink_prop_change, "PropertyChanged", "org.bluez.AudioSink", path_keyword="device")
		self.signals.Handle("dbus", self.bus, self.on_source_prop_change, "PropertyChanged", "org.bluez.AudioSource", path_keyword="device")
		self.signals.Handle("dbus", self.bus, self.on_hsp_prop_change, "PropertyChanged", "org.bluez.Headset", path_keyword="device")
		
		self.pulse_utils = PulseAudioUtils()
		self.signals.Handle(self.pulse_utils, "connected", self.on_pulse_connected)
		
	def on_pulse_connected(self, pa_utils):
		def modules_cb(modules):
			for k, v in modules.iteritems():
				if v["name"] == "module-bluetooth-discover":
					pa_utils.UnloadModule(k, lambda x: dprint(x))
			
		self.pulse_utils.ListModules(modules_cb)			
	
	def on_unload(self):
		self.signals.DisconnectAll()
		
	def on_source_prop_change(self, key, value, device):
		dprint(key, value)
		def load_cb(res):
			dprint(res)
			if res:
				SourceRedirector(res, device, self.pulse_utils)
			
		if key == "State":
			if value == "connected":
				if not device in self.connected_sources:
					self.connected_sources.append(device)
					self.pulse_utils.LoadModule("module-bluetooth-device", "path=%s profile=a2dp_source" % device, load_cb)
					
			elif value == "disconnected":
				if device in self.connected_sources:
					self.connected_sources.remove(device)		
		
	def on_sink_prop_change(self, key, value, device):
		if key == "Connected" and value:
			gobject.timeout_add(500, self.setup_pa, device, "a2dp")
		
	def on_hsp_prop_change(self, key, value, device):
		if key == "Connected" and value:
			gobject.timeout_add(500, self.setup_pa, device, "hsp")
		
	def setup_pa(self, device_path, profile):
		device = BluezDevice(device_path)
		props = device.GetProperties()
		
		def load_cb(res):
			dprint("Load result", res)
			if res < 0:
				dprint("Failed to load pulseaudio module")
				Notification(_("Bluetooth Audio"), 
							 _("Failed to initialize PulseAudio Bluetooth module. Bluetooth audio over PulseAudio will not work."), 
							 pixbuf=get_icon("gtk-dialog-error", 48), 
							 status_icon=self.Applet.Plugins.StatusIcon)				
			else:
				dprint("Pulseaudio module loaded successfully")
				Notification(_("Bluetooth Audio"), 
							 _("Successfully connected to a Bluetooth audio device. This device will now be available in the PulseAudio mixer"), 
							 pixbuf=get_icon("audio-card", 48), 
							 status_icon=self.Applet.Plugins.StatusIcon)			
		
		self.pulse_utils.LoadModule("module-bluetooth-device", "address=%s profile=%s" % (props["Address"], profile), load_cb)
