Warning: truncated output (original token count: 34148)
Total output lines: 2538

import React, { useState, useEffect, useCallback } from 'react';
import {
  getSettings,
  updateSettings,
  getBusinessVariables,
  saveBusinessVariables,
  BusinessVariable,
  listKnowledgeFiles,
  createManualLearning,
  uploadKnowledgeFile,
  uploadCredentialsFile,
  KnowledgeFile,
  ManualLearningEntry,
  getKnowledgeFile,
  saveKnowledgeFile,
  deleteKnowledgeFile,
  searchKnowledgeFile,
  purgeKnowledgeFile,
  SearchResultItem,
  getServices,
  saveServices,
  getSmsTemplate,
  saveSmsTemplate,
  getBookingReminderConfig,
  saveBookingReminderConfig,
  BookingReminderConfig,
  Service,
  getWorkingHours,
  saveWorkingHours,
  WorkingHourEntry,
  getMobileMessageConfig,
  saveMobileMessageConfig,
  getQARules,
  saveQARules,
  QARule,
  getFirstContactAutoresponder,
  saveFirstContactAutoresponder,
  FirstContactAutoresponderConfig,
  FirstContactAutoresponderSettings,
  clearPendingDrafts,
  clearReviewOnlyThreads
} from './api';
import {
  Key,
  Cpu,
  FileText,
  Calendar,
  UploadCloud,
  CheckCircle2,
  AlertCircle,
  Terminal,
  RefreshCw,
  File,
  Edit,
  Trash2,
  Search,
  X,
  BookOpen,
  DollarSign,
  Sliders,
  Plus,
  MessageSquare,
  Copy,
  Check,
  GripVertical,
  BellRing,
  Volume2
} from 'lucide-react';
import {
  getIncomingAlarmSettings,
  playAirRaidSiren,
  setIncomingAlarmEnabled,
  setIncomingAlarmVolume,
  stopIncomingAlarm,
  unlockIncomingAlarmAudio
} from './incomingMessageAlarm';
import OperationsAIChat from './OperationsAIChat';

const BUILT_IN_TEMPLATE_VARIABLES = [
  { key: 'current_time', token: '{current_time}', label: 'Current business time', scope: 'AI prompts', description: 'Current business time', required: false, required_status: 'optional' },
  { key: 'message', token: '{message}', label: 'Customer message', scope: 'User prompt', description: 'Incoming customer message text', required: true, required_status: 'required' },
  { key: 'knowledge', token: '{knowledge}', label: 'Live business context', scope: 'User prompt', description: 'Retrieved knowledge context', required: false, required_status: 'optional' },
  { key: 'slots', token: '{slots}', label: 'Calendar availability', scope: 'User prompt', description: 'Available calendar slots', required: false, required_status: 'optional' },
  { key: 'name', token: '{name}', label: 'Customer name', scope: 'Booking confirmation', description: 'Customer full or first name', required: true, required_status: 'required' },
  { key: 'service', token: '{service}', label: 'Booked service', scope: 'Booking confirmation', description: 'Selected service name', required: true, required_status: 'required' },
  { key: 'time', token: '{time}', label: 'Booking time', scope: 'Booking confirmation', description: 'Confirmed appointment time', required: true, required_status: 'required' },
];

const DEFAULT_BUSINESS_VARIABLES: BusinessVariable[] = [
  { key: 'provider_name', token: '{provider_name}', label: 'Provider name', value: '', description: 'Name of the service provider or practitioner', required: true, required_status: 'required' },
  { key: 'business_name', token: '{business_name}', label: 'Business name', value: '', description: 'Trading name of the business', required: false, required_status: 'optional' },
  { key: 'street_address', token: '{street_address}', label: 'Street address', value: '', description: 'Physical street address for appointments', required: false, required_status: 'optional' },
  { key: 'suburb', token: '{suburb}', label: 'Suburb', value: '', description: 'Locality or suburb for location inquiries', required: true, required_status: 'required' },
  { key: 'state', token: '{state}', label: 'State', value: '', description: 'State or territory', required: false, required_status: 'optional' },
  { key: 'postcode', token: '{postcode}', label: 'Postcode', value: '', description: 'Postal code', required: false, required_status: 'optional' },
  { key: 'website', token: '{website}', label: 'Website', value: '', description: 'Canonical website link or booking URL', required: true, required_status: 'required' },
  { key: 'business_phone', token: '{business_phone}', label: 'Business phone', value: '', description: 'Primary contact phone number', required: false, required_status: 'optional' },
  { key: 'email', token: '{email}', label: 'Email', value: '', description: 'Business contact email address', required: false, required_status: 'optional' },
  { key: 'booking_arrival_notes', token: '{booking_arrival_notes}', label: 'Booking arrival notes', value: '', description: 'Special instructions upon customer arrival', required: false, required_status: 'optional' },
  { key: 'booking_url', token: '{booking_url}', label: 'Booking URL', value: '', description: 'Direct link to online booking page', required: false, required_status: 'optional' },
];

const retryOnce = async <T,>(operation: () => Promise<T>): Promise<T> => {
  try {
    return await operation();
  } catch (firstError) {
    console.warn('Settings request failed, retrying once:', firstError);
    await new Promise((resolve) => window.setTimeout(resolve, 600));
    return operation();
  }
};


export default function SettingsView() {
  // Settings Form State
  const [apiKey, setApiKey] = useState('');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [userPrompt, setUserPrompt] = useState('');
  const [hasGoogleCreds, setHasGoogleCreds] = useState(false);
  const [showMessageAvatars, setShowMessageAvatars] = useState(true);
  const [savingMessageDisplay, setSavingMessageDisplay] = useState(false);
  const [clearingPendingDrafts, setClearingPendingDrafts] = useState(false);
  const [clearingReviewTags, setClearingReviewTags] = useState(false);
  const [catchUpLookbackDays, setCatchUpLookbackDays] = useState(3);
  const [savingCatchUpWindow, setSavingCatchUpWindow] = useState(false);
  const [incomingAlarmEnabled, setIncomingAlarmEnabledState] = useState(() => getIncomingAlarmSettings().enabled);
  const [incomingAlarmVolume, setIncomingAlarmVolumeState] = useState(() => getIncomingAlarmSettings().volume);
  const [testingIncomingAlarm, setTestingIncomingAlarm] = useState(false);
  const [businessVariables, setBusinessVariables] = useState<BusinessVariable[]>(DEFAULT_BUSINESS_VARIABLES);
  const [savingBusinessVariables, setSavingBusinessVariables] = useState(false);
  const [showVariableEditor, setShowVariableEditor] = useState(false);
  const [copiedVariableToken, setCopiedVariableToken] = useState<string | null>(null);
  const [settingsConnectionError, setSettingsConnectionError] = useState(false);

  // Lists & Loaders
  const [knowledgeFiles, setKnowledgeFiles] = useState<KnowledgeFile[]>([]);
  const [loadingSettings, setLoadingSettings] = useState(false);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [uploadingKnowledge, setUploadingKnowledge] = useState(false);
  const [uploadingCreds, setUploadingCreds] = useState(false);
  const [learningTopic, setLearningTopic] = useState('');
  const [learningGuidance, setLearningGuidance] = useState('');
  const [savingLearning, setSavingLearning] = useState(false);
  const [lastSavedLearning, setLastSavedLearning] = useState<ManualLearningEntry | null>(null);

  // Modal states for File Editor & Moderation
  const [activeEditFile, setActiveEditFile] = useState<string | null>(null);
  const [activeEditFileSize, setActiveEditFileSize] = useState<number>(0);
  const [editorContent, setEditorContent] = useState('');
  const [modalTab, setModalTab] = useState<'editor' | 'moderator'>('editor');
  const [loadingFileContent, setLoadingFileContent] = useState(false);
  const [savingFileContent, setSavingFileContent] = useState(false);
  
  // Moderation state
  const [modQuery, setModQuery] = useState('');
  const [modResults, setModResults] = useState<SearchResultItem[]>([]);
  const [searchingMod, setSearchingMod] = useState(false);
  const [totalMatches, setTotalMatches] = useState(0);

  // Services & SMS Template states
  const [services, setServices] = useState<Service[]>([]);
  const [draggedIndex, setDraggedIndex] = useState<number | null>(null);
  const [smsTemplate, setSmsTemplate] = useState('');
  const [bookingReminder, setBookingReminder] = useState<BookingReminderConfig>({
    enabled: true,
    minutesBefore: 60,
    template: 'Hi {name}, just a reminder that your booking for {service} is at {time}. See you then. - {provider}',
  });
  const [savingBookingReminder, setSavingBookingReminder] = useState(false);
  const [savingServices, setSavingServices] = useState(false);
  const [savingSmsTemplate, setSavingSmsTemplate] = useState(false);


  // New service form state
  const [newServiceName, setNewServiceName] = useState('');
  const [newServiceDesc, setNewServiceDesc] = useState('');
  const [newServicePrice, setNewServicePrice] = useState(100);
  const [newServiceDuration, setNewServiceDuration] = useState(60);
  const [newServiceShowDuration, setNewServiceShowDuration] = useState(true);
  const [newServiceLineKey, setNewServiceLineKey] = useState<'primary' | 'secondary'>('primary');

  // Edit service form state
  const [editingServiceId, setEditingServiceId] = useState<string | null>(null);
  const [editServiceName, setEditServiceName] = useState('');
  const [editServiceDesc, setEditServiceDesc] = useState('');
  const [editServicePrice, setEditServicePrice] = useState(100);
  const [editServiceDuration, setEditServiceDuration] = useState(60);
  const [editServiceShowDuration, setEditServiceShowDuration] = useState(true);
  const [editServiceLineKey, setEditServiceLineKey] = useState<'primary' | 'secondary'>('primary');


  // Working hours state
  const [workingHours, setWorkingHours] = useState<WorkingHourEntry[]>([]);
  const [savingWorkingHours, setSavingWorkingHours] = useState(false);

  // MobileMessage SMS Gateway state
  const [mmUsername, setMmUsername] = useState('');
  const [mmPassword, setMmPassword] = useState('');
  const [mmHasPassword, setMmHasPassword] = useState(false);
  const [mmSender, setMmSender] = useState('');
  const [savingMmConfig, setSavingMmConfig] = useState(false);
  const [copiedWebhook, setCopiedWebhook] = useState(false);
  const [copiedEmbed, setCopiedEmbed] = useState(false);
  const [copiedIframe, setCopiedIframe] = useState(false);

  // Q&A Rules state
  const [qaRules, setQaRules] = useState<QARule[]>([]);
  const [newRuleTrigger, setNewRuleTrigger] = useState('');
  const [newRuleReply, setNewRuleReply] = useState('');
  const [savingQaRules, setSavingQaRules] = useState(false);

  // First-contact auto-responder state
  const [firstContactConfig, setFirstContactConfig] = useState<FirstContactAutoresponderSettings>({
    accounts: {
      primary: { enabled: false, cooldownDays: 30, delaySeconds: 0, message: '' },
      secondary: { enabled: false, cooldownDays: 30, delaySeconds: 0, message: '' }
    },
    labels: { primary: 'Line 1', secondary: 'Line 2' }
  });
  const [savingFirstContact, setSavingFirstContact] = useState(false);

  // Banners
  const [banner, setBanner] = useState<{ type: 'success' | 'error'; message: string } | null>(null);

  const triggerBanner = (type: 'success' | 'error', message: string) => {
    setBanner({ type, message });
    setTimeout(() => setBanner(null), 5000);
  };

  // Fetch initial data — load independently so one failure doesn't blank everything
  const loadAllSettings = useCallback(async () => {
    setLoadingSettings(true);
    try {
      const settingsData = await retryOnce(getSettings);
      setApiKey(settingsData.openaiApiKey);
      setSystemPrompt(settingsData.systemPrompt);
      setUserPrompt(settingsData.userPrompt);
      setHasGoogleCreds(settingsData.hasGoogleCredentials);
      setShowMessageAvatars(settingsData.showMessageAvatars !== false);
      setCatchUpLookbackDays(settingsData.catchUpLookbackDays ?? 3);
      setSettingsConnectionError(false);

    } catch (err) {
      console.error('settings fetch failed:', err);
      setSettingsConnectionError(true);
      triggerBanner('error', 'Failed to load system settings from backend.');
    } finally {
      setLoadingSettings(false);
    }
    try { setServices(await retryOnce(getServices)); } catch (e) { console.error('services fetch failed:', e); }
    try { setBusinessVariables(await retryOnce(getBusinessVariables)); } catch (e) { console.error('business variables fetch failed:', e); }
    try { setSmsTemplate((await retryOnce(getSmsTemplate)).template); } catch (e) { console.error('sms template fetch failed:', e); }
    try { setBookingReminder(await retryOnce(getBookingReminderConfig)); } catch (e) { console.error('booking reminder settings fetch failed:', e); }
    try { setWorkingHours(await retryOnce(getWorkingHours)); } catch (e) { console.error('working hours fetch failed:', e); }
    try {
      const mm = await retryOnce(getMobileMessageConfig);
      setMmUsername(mm.username || '');
      setMmPassword(mm.password || '');
      setMmHasPassword(!!mm.hasPassword);
      setMmSender(mm.sender || '');
    } catch (e) { console.error('MobileMessage fetch failed:', e); }
    try { setQaRules(await retryOnce(getQARules)); } catch (e) { console.error('qa rules fetch failed:', e); }
    try { setFirstContactConfig(await retryOnce(getFirstContactAutoresponder)); } catch (e) { console.error('first-contact auto-responder fetch failed:', e); }
  }, []);

  const fetchKnowledgeFilesList = useCallback(async () => {
    setLoadingFiles(true);
    try {
      const files = await retryOnce(listKnowledgeFiles);
      setKnowledgeFiles(files);
    } catch (error) {
      console.error(error);
      triggerBanner('error', 'Knowledge documents could not be loaded. Use Refresh to try again.');
    } finally {
      setLoadingFiles(false);
    }
  }, []);

  useEffect(() => {
    loadAllSettings();
    fetchKnowledgeFilesList();
  }, [loadAllSettings, fetchKnowledgeFilesList]);

  // Handle updates
  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    setSavingSettings(true);
    try {
      await updateSettings({
        openaiApiKey: apiKey.trim(),
        systemPrompt: systemPrompt.trim(),
        userPrompt: userPrompt.trim()
      });
      triggerBanner('success', 'Prompt configurations and API key updated successfully.');
      await loadAllSettings(); // Refresh settings to show obfuscated key
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to update settings configurations.');
    } finally {
      setSavingSettings(false);
    }
  };

  const handleMessageAvatarToggle = async () => {
    const nextValue = !showMessageAvatars;
    setShowMessageAvatars(nextValue);
    setSavingMessageDisplay(true);
    try {
      await updateSettings({ showMessageAvatars: nextValue });
      triggerBanner('success', `Message avatars ${nextValue ? 'enabled' : 'hidden'}.`);
    } catch (err) {
      console.error(err);
      setShowMessageAvatars(!nextValue);
      triggerBanner('error', 'Failed to update message avatar setting.');
    } finally {
      setSavingMessageDisplay(false);
    }
  };

  const handleClearPendingDrafts = async () => {
    const confirmed = window.confirm(
      'Remove every pending AI draft waiting for approval? This cannot be undone.'
    );
    if (!confirmed) return;

    setClearingPendingDrafts(true);
    try {
      const result = await clearPendingDrafts();
      triggerBanner(
        'success',
        result.removedDrafts === 0
          ? 'There were no pending AI drafts to remove.'
          : `Removed ${result.removedDrafts} pending AI draft${result.removedDrafts === 1 ? '' : 's'} from ${result.affectedThreads} conversation${result.affectedThreads === 1 ? '' : 's'}.`
      );
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to clear pending AI drafts.');
    } finally {
      setClearingPendingDrafts(false);
    }
  };

  const handleClearReviewTags = async () => {
    const confirmed = window.confirm(
      'Clear review tags that have no pending draft? This will not send messages or remove drafts.'
    );
    if (!confirmed) return;

    setClearingReviewTags(true);
    try {
      const result = await clearReviewOnlyThreads();
      triggerBanner(
        'success',
        result.clearedThreads === 0
          ? 'There were no review-only tags to clear.'
          : `Cleared ${result.clearedThreads} review tag${result.clearedThreads === 1 ? '' : 's'}. Pending drafts were left untouched.`
      );
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to clear review tags.');
    } finally {
      setClearingReviewTags(false);
    }
  };

  const handleSaveCatchUpWindow = async () => {
    const nextValue = Math.min(30, Math.max(1, Math.round(catchUpLookbackDays || 1)));
    setCatchUpLookbackDays(nextValue);
    setSavingCatchUpWindow(true);
    try {
      await updateSettings({ catchUpLookbackDays: nextValue });
      triggerBanner('success', `Catch-up will only consider messages from the last ${nextValue} day${nextValue === 1 ? '' : 's'}.`);
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to save the catch-up window.');
    } finally {
      setSavingCatchUpWindow(false);
    }
  };

  const handleIncomingAlarmToggle = async () => {
    const nextValue = !incomingAlarmEnabled;
    if (nextValue) {
      try {
        await unlockIncomingAlarmAudio();
      } catch (error) {
        console.error(error);
        triggerBanner('error', 'This browser could not enable audio. Check its sound permissions.');
        return;
      }
    } else {
      stopIncomingAlarm();
      setTestingIncomingAlarm(false);
    }
    setIncomingAlarmEnabled(nextValue);
    setIncomingAlarmEnabledState(nextValue);
    triggerBanner('success', `Customer arrival siren ${nextValue ? 'enabled' : 'disabled'} on this device.`);
  };

  const handleIncomingAlarmVolume = (volume: number) => {
    setIncomingAlarmVolume(volume);
    setIncomingAlarmVolumeState(volume);
  };

  const handleTestIncomingAlarm = async () => {
    if (testingIncomingAlarm) {
      stopIncomingAlarm();
      setTestingIncomingAlarm(false);
      return;
    }
    try {
      await unlockIncomingAlarmAudio();
      await playAirRaidSiren(incomingAlarmVolume, 4500);
      setTestingIncomingAlarm(true);
      window.setTimeout(() => setTestingIncomingAlarm(false), 4700);
    } catch (error) {
      console.error(error);
      triggerBanner('error', 'The test sound was blocked. Check this browser tab\'s sound permission.');
    }
  };

  const updateBusinessVariable = (index: number, field: keyof BusinessVariable, value: string) => {
    setBusinessVariables((current) => current.map((item, itemIndex) => {
      if (itemIndex !== index) return item;
      const nextValue = field === 'key'
        ? value.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '')
        : value;
      return { ...item, [field]: nextValue };
    }));
  };

  const addBusinessVariable = () => {
    const existing = new Set(businessVariables.map((item) => item.key));
    let suffix = 1;
    let key = 'custom_variable';
    while (existing.has(key)) {
      suffix += 1;
      key = `custom_variable_${suffix}`;
    }
    setBusinessVariables((current) => [...current, { key, label: 'Custom variable', value: '' }]);
  };

  const removeBusinessVariable = (index: number) => {
    setBusinessVariables((current) => current.filter((_, itemIndex) => itemIndex !== index));
  };

  const copyVariableToken = async (token: string) => {
    await navigator.clipboard.writeText(`{${token}}`);
    setCopiedVariableToken(token);
    window.setTimeout(() => setCopiedVariableToken(null), 1800);
  };

  const handleSaveBusinessVariables = async () => {
    const invalid = businessVariables.find((item) => !/^[a-z][a-z0-9_]*$/.test(item.key) || !item.label.trim());
    if (invalid) {
      triggerBanner('error', 'Every variable needs a label and a key beginning with a letter.');
      return;
    }
    if (new Set(businessVariables.map((item) => item.key)).size !== businessVariables.length) {
      triggerBanner('error', 'Variable keys must be unique.');
      return;
    }
    setSavingBusinessVariables(true);
    try {
      const result = await saveBusinessVariables(businessVariables);
      setBusinessVariables(result.variables);
      triggerBanner('success', 'Business variables saved and available to the AI immediately.');
    } catch (err) {
      console.error(err);
      triggerBanner('error', err instanceof Error ? err.message : 'Failed to save business variables.');
    } finally {
      setSavingBusinessVariables(false);
    }
  };

  const handleKnowledgeUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingKnowledge(true);
    try {
      await uploadKnowledgeFile(file);
      triggerBanner('success', `Successfully uploaded knowledge file "${file.name}"`);
      await fetchKnowledgeFilesList();
    } catch (err) {
      console.error(err);
      triggerBanner('error', `Failed to upload knowledge file "${file.name}"`);
    } finally {
      setUploadingKnowledge(false);
      e.target.value = ''; // clear input
    }
  };

  const handleCreateLearning = async (e: React.FormEvent) => {
    e.preventDefault();
    const topic = learningTopic.trim();
    const guidance = learningGuidance.trim();
    if (!topic || !guidance) return;

    setSavingLearning(true);
    try {
      const result = await createManualLearning(topic, guidance);
      setLastSavedLearning(result.entry);
      setLearningTopic('');
      setLearningGuidance('');
      triggerBanner('success', `Learning saved to ${result.filename} and is available to the AI now.`);
      await fetchKnowledgeFilesList();
    } catch (err) {
      console.error(err);
      triggerBanner(
        'error',
        err instanceof Error ? err.message : 'The learning could not be structured. Nothing was saved.',
      );
    } finally {
      setSavingLearning(false);
    }
  };

  const handleCredentialsUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingCreds(true);
    try {
      await uploadCredentialsFile(file);
      triggerBanner('success', 'Google credentials file updated. Re-initializing calendar...');
      setHasGoogleCreds(true);
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to upload credentials file.');
    } finally {
      setUploadingCreds(false);
      e.target.value = '';
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const handleOpenEdit = async (file: KnowledgeFile) => {
    setActiveEditFile(file.name);
    setActiveEditFileSize(file.sizeBytes);
    setModalTab('editor');
    setEditorContent('');
    setModQuery('');
    setModResults([]);
    setTotalMatches(0);
    
    setLoadingFileContent(true);
    try {
      const res = await getKnowledgeFile(file.name);
      if (file.sizeBytes < 500 * 1024) {
        setEditorContent(res.content);
      } else {
        const lines = res.content.split('\n');
        const preview = lines.slice(0, 100).join('\n');
        setEditorContent(preview + (lines.length > 100 ? '\n... [truncated - file is too large]' : ''));
      }
    } catch (err) {
      console.error(err);
      triggerBanner('error', `Failed to load content for file "${file.name}"`);
    } finally {
      setLoadingFileContent(false);
    }
  };

  const handleSaveFileContent = async () => {
    if (!activeEditFile) return;
    setSavingFileContent(true);
    try {
      await saveKnowledgeFile(activeEditFile, editorContent);
      triggerBanner('success', `File "${activeEditFile}" saved successfully.`);
      setActiveEditFile(null);
      await fetchKnowledgeFilesList();
    } catch (err) {
      console.error(err);
      triggerBanner('error', `Failed to save file content.`);
    } finally {
      setSavingFileContent(false);
    }
  };

  const handleDeleteFile = async (filename: string) => {
    if (!window.confirm(`Are you sure you want to permanently delete "${filename}"? This will reload your knowledge base chunks.`)) {
      return;
    }
    try {
      await deleteKnowledgeFile(filename);
      triggerBanner('success', `File "${filename}" deleted successfully.`);
      await fetchKnowledgeFilesList();
      if (activeEditFile === filename) {
        setActiveEditFile(null);
      }
    } catch (err) {
      console.error(err);
      triggerBanner('error', `Failed to delete file "${filename}".`);
    }
  };

  const handleSearchMod = async () => {
    if (!activeEditFile || !modQuery.trim()) return;
    setSearchingMod(true);
    try {
      const res = await searchKnowledgeFile(activeEditFile, modQuery.trim());
      setModResults(res.results);
      setTotalMatches(res.totalMatches);
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Search failed on backend.');
    } finally {
      setSearchingMod(false);
    }
  };

  const handlePurgeIndex = async (index: number) => {
    if (!activeEditFile) return;
    if (!window.confirm("Are you sure you want to delete this row?")) return;
    try {
      const res = await purgeKnowledgeFile(activeEditFile, undefined, [index]);
      triggerBanner('success', `Successfully purged ${res.purgedCount} row.`);
      await handleSearchMod();
      await fetchKnowledgeFilesList();
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Purge request failed.');
    }
  };

  const handleBulkPurgeQuery = async (queryToPurge: string) => {
    if (!activeEditFile || !queryToPurge.trim()) return;
    if (!window.confirm(`Are you sure you want to delete ALL messages containing "${queryToPurge}"? This cannot be undone.`)) {
      return;
    }
    try {
      const res = await purgeKnowledgeFile(activeEditFile, queryToPurge.trim());
      triggerBanner('success', `Successfully bulk-purged ${res.purgedCount} rows.`);
      setModQuery('');
      setModResults([]);
      setTotalMatches(0);
      await fetchKnowledgeFilesList();
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Bulk purge request failed.');
    }
  };

  const handleBlacklistPurge = async () => {
    if (!activeEditFile) return;
    const terms = ["bank transfer", "account number", "card number", "credit card", "bank acc"];
    if (!window.confirm(`Are you sure you want to run the automatic blacklist cleaner? This will search and purge all entries containing any of these keywords: ${terms.join(', ')}.`)) {
      return;
    }
    try {
      let totalPurged = 0;
      for (const term of terms) {
        const res = await purgeKnowledgeFile(activeEditFile, term);
        totalPurged += res.purgedCount;
      }
      triggerBanner('success', `Blacklist clean complete. Purged a total of ${totalPurged} rows.`);
      await fetchKnowledgeFilesList();
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to run blacklist clean.');
    }
  };

  const handleDownloadFullFile = async () => {
    if (!activeEditFile) return;
    try {
      const res = await getKnowledgeFile(activeEditFile);
      const element = document.createElement("a");
      const file = new Blob([res.content], { type: 'application/octet-stream' });
      element.href = URL.createObjectURL(file);
      element.download = activeEditFile;
      document.body.appendChild(element);
      element.click();
      document.body.removeChild(element);
      triggerBanner('success', `File "${activeEditFile}" download started.`);
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to download file from backend.');
    }
  };

  const handleSaveSmsTemplate = async () => {
    setSavingSmsTemplate(true);
    try {
      await saveSmsTemplate(smsTemplate);
      triggerBanner('success', 'SMS Confirmation Template saved successfully.');
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to save SMS confirmation template.');
    } finally {
      setSavingSmsTemplate(false);
    }
  };

  const handleSaveBookingReminder = async () => {
    setSavingBookingReminder(true);
    try {
      await saveBookingReminderConfig(bookingReminder);
      triggerBanner('success', 'Booking reminder settings saved.');
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to save booking reminder settings.');
    } finally {
      setSavingBookingReminder(false);
    }
  };

  const handleAddService = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newServiceName.trim()) return;
    const newService: Service = {
      id: `srv_${Date.now()}`,
      name: newServiceName,
      description: newServiceDesc,
      price: newServicePrice,
      duration: newServiceDuration,
      showDuration: newServiceShowDuration,
      lineKey: newServiceLineKey,
    };
    setServices([...services, newService]);
    setNewServiceName('');
    setNewServiceDesc('');
    setNewServicePrice(100);
    setNewServiceDuration(60);
    setNewServiceShowDuration(true);
    setNewServiceLineKey('primary');
    triggerBanner('success', 'Service added. Click Save Services to apply.');
  };

  const handleDeleteService = (id: string) => {
    setServices(services.filter(s => s.id !== id));
    triggerBanner('success', 'Service removed. Click Save Services to apply.');
  };

  const handleDragStart = (e: React.DragEvent, index: number) => {
    setDraggedIndex(index);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', index.toString());
  };

  const handleDragOver = (e: React.DragEvent, index: number) => {
    e.preventDefault();
    if (draggedIndex === null || draggedIndex === index) return;
    
    const items = [...services];
    const draggedItem = items[draggedIndex];
    items.splice(draggedIndex, 1);
    items.splice(index, 0, draggedItem);
    
    setDraggedIndex(index);
    setServices(items);
  };

  const handleDragEnd = () => {
    setDraggedIndex(null);
    triggerBanner('success', 'Services reordered. Click Save Services to apply.');
  };

  const startEditService = (srv: Service) => {
    setEditingServiceId(srv.id);
    setEditServiceName(srv.name);
    setEditServiceDesc(srv.description);
    setEditServicePrice(srv.price);
    setEditServiceDuration(srv.duration);
    setEditServiceShowDuration(srv.showDuration !== false);
    setEditServiceLineKey(srv.lineKey || 'primary');
  };

  const handleUpdateService = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingServiceId) return;

    setServices(services.map(srv => {
      if (srv.id === editingServiceId) {
        return {
          ...srv,
          name: editServiceName,
          description: editServiceDesc,
          price: editServicePrice,
          duration: editServiceDuration,
          showDuration: editServiceShowDuration,
          lineKey: editServiceLineKey,
        };
      }
      return srv;
    }));

    setEditingServiceId(null);
    triggerBanner('success', 'Service updated. Click Save Services to apply.');
  };

  const cancelEditService = () => {
    setEditingServiceId(null);
  };

  const handleSaveServices = async () => {
    setSavingServices(true);
    try {
      await saveServices(services);
      triggerBanner('success', 'Services configured successfully on backend.');
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to save services configuration.');
    } finally {
      setSavingServices(false);
    }
  };

  const handleAddQaRule = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRuleTrigger.trim() || !newRuleReply.trim()) return;
    const newRule: QARule = {
      id: Math.random().toString(36).substr(2, 9),
      trigger: newRuleTrigger.trim(),
      reply: newRuleReply.trim()
    };
    setQaRules([...qaRules, newRule]);
    setNewRuleTrigger('');
    setNewRuleReply('');
    triggerBanner('success', 'Q&A rule added locally. Remember to click Save Rules.');
  };

  const handleDeleteQaRule = (id: string) => {
    setQaRules(qaRules.filter(r => r.id !== id));
    triggerBanner('success', 'Q&A rule removed locally. Remember to click Save Rules.');
  };

  const handleSaveQaRules = async () => {
    setSavingQaRules(true);
    try {
      await saveQARules(qaRules);
      triggerBanner('success', 'Q&A Rules configuration saved successfully.');
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to save Q&A Rules configuration.');
    } finally {
      setSavingQaRules(false);
    }
  };

  const handleSaveFirstContact = async () => {
    for (const key of ['primary', 'secondary'] as const) {
      const account = firstContactConfig.accounts[key];
      if (account.enabled && !account.message.trim()) {
        triggerBanner('error', `Enter a first-contact reply message for ${firstContactConfig.labels[key]} before enabling it.`);
        return;
      }
    }
    setSavingFirstContact(true);
    try {
      const config = {
        ...firstContactConfig,
        accounts: {
          primary: normalizeFirstContactAccount(firstContactConfig.accounts.primary),
          secondary: normalizeFirstContactAccount(firstContactConfig.accounts.secondary)
        }
      };
      await saveFirstContactAutoresponder(config);
      setFirstContactConfig(config);
      triggerBanner('success', 'First-contact auto-responder saved.');
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to save the first-contact auto-responder.');
    } finally {
      setSavingFirstContact(false);
    }
  };

  const normalizeFirstContactAccount = (
    account: FirstContactAutoresponderConfig
  ): FirstContactAutoresponderConfig => ({
    ...account,
    cooldownDays: Math.max(1, Math.min(3650, Number(account.cooldownDays) || 1)),
    delaySeconds: Math.max(0, Math.min(3600, Number(account.delaySeconds) || 0)),
    message: account.message.trim()
  });

  const updateFirstContactAccount = (
    key: 'primary' | 'secondary',
    update: Partial<FirstContactAutoresponderConfig>
  ) => {
    setFirstContactConfig(current => ({
      ...current,
      accounts: {
        ...current.accounts,
        [key]: { ...current.accounts[key], ...update }
      }
    }));
  };

  const updateWorkingHourField = (index: number, field: keyof WorkingHourEntry, value: string | boolean) => {
    setWorkingHours(prev =>
      prev.map((entry, i) => i === index ? { ...entry, [field]: value } : entry)
    );
  };

  const handleSaveWorkingHours = async () => {
    setSavingWorkingHours(true);
    try {
      await saveWorkingHours(workingHours);
      triggerBanner('success', 'Working hours saved. Slots will update immediately.');
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to save working hours.');
    } finally {
      setSavingWorkingHours(false);
    }
  };

  const handleSaveMobileMessage = async () => {
    setSavingMmConfig(true);
    try {
      await saveMobileMessageConfig({
        username: mmUsername,
        password: mmPassword,
        sender: mmSender,
        enabled: Boolean(mmUsername.trim() && (mmPassword.trim() || mmHasPassword))
      });
      if (mmPassword.trim()) setMmHasPassword(true);
      triggerBanner('success', 'MobileMessage SMS configuration saved.');
    } catch (err) {
      console.error(err);
      triggerBanner('error', 'Failed to save MobileMessage configuration.');
    } finally {
      setSavingMmConfig(false);
    }
  };

  const webhookUrl = `${window.location.origin}/webhooks/sms`;

  const copyWebhookUrl = () => {
    navigator.clipboard.writeText(webhookUrl);
    setCopiedWebhook(true);
    setTimeout(() => setCopiedWebhook(false), 3000);
  };

  const widgetBaseUrl = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'https://assistant-ui-hub.fly.dev'
    : window.location.origin;
  const bookingInlineScriptUrl = `${widgetBaseUrl}/booking-inline.js`;

  const embedScriptCode = `<div id="booking-container" data-api-base="${widgetBaseUrl}"></div>\n<script type="module" src="${bookingInlineScriptUrl}"></script>`;

  const embedIframeCode = `<iframe src="${widgetBaseUrl}/booking" width="100%" height="800" style="border:none; border-radius:12px;"></iframe>`;

  const copyScriptCode = () => {
    navigator.clipboard.writeText(embedScriptCode);
    setCopiedEmbed(true);
    setTimeout(() => setCopiedEmbed(false), 3000);
  };

  const copyIframeCode = () => {
    navigator.clipboard.writeText(embedIframeCode);
    setCopiedIframe(true);
    setTimeout(() => setCopiedIframe(false), 3000);
  };

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50 p-6 font-sans">
      <div className="max-w-4xl mx-auto flex flex-col gap-6">
        
        {/* Title */}
        <div className="flex justify-between items-center pb-2 border-b border-slate-200">
          <div>
            <h1 className="text-xl font-bold text-slate-900">System Configuration</h1>
            <p className="text-xs text-slate-500 mt-0.5">Manage agent prompts, RAG documents, and calendar bindings.</p>
          </div>
          <button
            onClick={() => {
              loadAllSettings();
              fetchKnowledgeFilesList();
            }}
            className="flex items-center gap-1.5 bg-white border border-slate-350 hover:bg-slate-50 text-slate-700 text-xs px-3 py-1.5 rounded-lg shadow-sm font-semibold transition-all cursor-pointer"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            Refresh
          </button>
        </div>

        {/* Quick Links */}
        <div className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col md:flex-row gap-2 md:gap-4 text-xs shadow-xs">
          <span className="font-bold text-slate-800 shrink-0">Quick Access Links:</span>
          <div className="flex flex-col gap-2 flex-wrap min-w-0">
            <a href="https://carnival-cute-superintendent-act.trycloudflare.com" target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:text-indigo-850 hover:underline break-all font-semibold">
              https://carnival-cute-superintendent-act.trycloudflare.com
            </a>
            <a href="https://sequence-converted-causing-mat.trycloudflare.com" target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:text-indigo-850 hover:underline break-all font-semibold">
              https://sequence-converted-causing-mat.trycloudflare.com
            </a>
            <a href="https://app.mobilemessage.com.au/" target="_blank…14148 tokens truncated…ation ? 'bg-indigo-600' : 'bg-slate-300'}`}
                        >
                          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${newServiceShowDuration ? 'translate-x-4' : 'translate-x-0'}`} />
                        </button>
                        <span className="text-[10px] text-slate-600 font-semibold">
                          {newServiceShowDuration ? 'Show duration on booking form' : 'Hide duration on booking form'}
                        </span>
                      </label>
                      <button
                        type="submit"
                        className="bg-slate-800 hover:bg-slate-700 text-white font-bold text-[11px] px-3.5 py-1.5 rounded shadow-sm transition-all cursor-pointer border border-transparent"
                      >
                        Add to list
                      </button>
                    </div>
                  </form>

                  {/* Save services control */}
                  <div className="flex justify-end pt-2 border-t border-slate-150">
                    <button
                      onClick={handleSaveServices}
                      disabled={savingServices}
                      className="bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs px-4 py-2.5 rounded-lg shadow-sm transition-all cursor-pointer border border-transparent"
                    >
                      {savingServices ? 'Saving services list...' : 'Save Services List'}
                    </button>
                  </div>

                  {/* Standalone Booking Form Embedding Snippet */}
                  <div className="mt-4 flex flex-col gap-4 font-sans">

                    {/* Option 1: Native inline booking interface (Recommended) */}
                    <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl flex flex-col gap-2">
                      <div className="flex justify-between items-center">
                        <span className="font-bold text-slate-700 text-xs flex items-center gap-1.5">
                          <Calendar className="w-3.5 h-3.5 text-indigo-500" /> Option A: Native Inline Embed (Recommended)
                        </span>
                        <button
                          onClick={copyScriptCode}
                          className="flex items-center gap-1 text-[10px] font-bold text-indigo-700 hover:text-indigo-900 bg-white px-2.5 py-1 rounded border border-indigo-200 shadow-2xs transition-colors cursor-pointer"
                        >
                          {copiedEmbed ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
                          {copiedEmbed ? 'Copied!' : 'Copy Script'}
                        </button>
                      </div>
                      <pre className="text-[10px] font-mono text-slate-700 bg-white p-2.5 rounded border border-slate-200 overflow-x-auto whitespace-pre leading-relaxed select-all">
                        {embedScriptCode}
                      </pre>
                      <p className="text-[9px] text-slate-500 leading-relaxed">
                        Renders native booking cards directly on your website without an iframe. Services, prices, durations and availability are loaded live from this booking application whenever the page opens.
                      </p>
                    </div>

                    {/* Option 2: Standard Iframe Fallback */}
                    <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl flex flex-col gap-2">
                      <div className="flex justify-between items-center">
                        <span className="font-bold text-slate-700 text-xs flex items-center gap-1.5">
                          <Calendar className="w-3.5 h-3.5 text-slate-400" /> Option B: Simple Iframe Embed
                        </span>
                        <button
                          onClick={copyIframeCode}
                          className="flex items-center gap-1 text-[10px] font-bold text-indigo-700 hover:text-indigo-900 bg-white px-2.5 py-1 rounded border border-indigo-200 shadow-2xs transition-colors cursor-pointer"
                        >
                          {copiedIframe ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
                          {copiedIframe ? 'Copied!' : 'Copy Iframe'}
                        </button>
                      </div>
                      <code className="text-[10px] font-mono text-slate-700 bg-white p-2.5 rounded border border-slate-200 select-all break-all leading-normal">
                        {embedIframeCode}
                      </code>
                      <p className="text-[9px] text-slate-500">
                        Traditional static height iframe. Use this if your website builder blocks custom Javascript/scripts.
                      </p>
                    </div>

                  </div>

                </div>

              </div>
            </div>

        </div>

        {/* Working Hours Card */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Calendar className="w-5 h-5 text-indigo-600" />
              <h2 className="font-bold text-slate-800 text-sm">Available Hours</h2>
            </div>
            <button
              onClick={handleSaveWorkingHours}
              disabled={savingWorkingHours || workingHours.length === 0}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white font-bold text-[11px] px-3.5 py-1.5 rounded-lg shadow-sm transition-all cursor-pointer"
            >
              {savingWorkingHours ? 'Saving...' : 'Save Hours'}
            </button>
          </div>

          <div className="divide-y divide-slate-100">
            {workingHours.length === 0 ? (
              <div className="py-10 text-center text-xs text-slate-400">Loading working hours...</div>
            ) : (
              workingHours.map((entry, i) => (
                <div
                  key={entry.day}
                  className={`flex items-center gap-3 px-4 py-3.5 transition-colors ${entry.enabled ? 'bg-white' : 'bg-slate-50/60'}`}
                >
                  <button
                    onClick={() => updateWorkingHourField(i, 'enabled', !entry.enabled)}
                    className={`relative shrink-0 w-10 h-5 rounded-full transition-colors cursor-pointer ${entry.enabled ? 'bg-indigo-600' : 'bg-slate-300'}`}
                    aria-label={`Toggle ${entry.day}`}
                  >
                    <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow-sm transition-transform ${entry.enabled ? 'translate-x-5' : 'translate-x-0'}`} />
                  </button>
                  <span className={`w-24 text-xs font-bold shrink-0 font-sans ${entry.enabled ? 'text-slate-800' : 'text-slate-400'}`}>
                    {entry.day}
                  </span>
                  {entry.enabled ? (
                    <div className="flex items-center gap-2 flex-1 flex-wrap">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-slate-500 shrink-0">From</span>
                        <input
                          type="time"
                          value={entry.open}
                          onChange={(e) => updateWorkingHourField(i, 'open', e.target.value)}
                          className="text-xs border border-slate-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-slate-50 font-mono"
                        />
                      </div>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-slate-500 shrink-0">To</span>
                        <input
                          type="time"
                          value={entry.close}
                          onChange={(e) => updateWorkingHourField(i, 'close', e.target.value)}
                          className="text-xs border border-slate-300 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-slate-50 font-mono"
                        />
                      </div>
                      {/* 24hr shortcut */}
                      <button
                        onClick={() => {
                          updateWorkingHourField(i, 'open', '00:00');
                          updateWorkingHourField(i, 'close', '23:59');
                        }}
                        className="ml-auto shrink-0 text-[10px] font-bold px-2.5 py-1.5 rounded-lg bg-slate-100 hover:bg-indigo-100 hover:text-indigo-700 text-slate-500 border border-slate-200 transition-colors cursor-pointer"
                      >
                        24 hrs
                      </button>
                    </div>
                  ) : (
                    <span className="text-[10px] text-slate-400 italic">Day off</span>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* MobileMessage SMS Gateway Card */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <MessageSquare className="w-5 h-5 text-indigo-600" />
              <div>
                <h2 className="font-bold text-slate-800 text-sm">Mobile Message Gateway (Australia)</h2>
                <p className="text-[10px] text-slate-500 font-sans">Connect your mobilemessage.com.au account to send & receive real SMS.</p>
              </div>
            </div>
            <button
              onClick={handleSaveMobileMessage}
              disabled={savingMmConfig}
              className="bg-indigo-600 hover:bg-indigo-700 disabled:opacity-40 text-white font-bold text-[11px] px-3.5 py-1.5 rounded-lg shadow-sm transition-all cursor-pointer shrink-0"
            >
              {savingMmConfig ? 'Saving...' : 'Save Gateway'}
            </button>
          </div>

          <div className="p-4 flex flex-col gap-4 font-sans text-xs">
            {/* Gateway status — delivery follows the single global AI switch. */}
            <div className="flex items-center justify-between p-3 bg-slate-50 rounded-xl border border-slate-200">
              <div>
                <span className="font-bold text-slate-800">Live SMS Gateway</span>
                <p className="text-[10px] text-slate-500 mt-0.5">SMS delivery follows the global AI on/off control above.</p>
              </div>
              <span className={`text-[10px] font-extrabold px-2.5 py-1 rounded-full ${
                mmUsername.trim() && (mmPassword.trim() || mmHasPassword)
                  ? 'bg-emerald-100 text-emerald-700'
                  : 'bg-amber-100 text-amber-700'
              }`}>
                {mmUsername.trim() && (mmPassword.trim() || mmHasPassword) ? 'Connected' : 'Not configured'}
              </span>
            </div>

            {/* Credentials fields */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">API Username</label>
                <input
                  type="text"
                  value={mmUsername}
                  onChange={(e) => setMmUsername(e.target.value)}
                  placeholder="e.g. user123"
                  className="text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-slate-50"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">API Password</label>
                <input
                  type="password"
                  value={mmPassword}
                  onChange={(e) => setMmPassword(e.target.value)}
                  placeholder={mmHasPassword ? 'Saved securely—enter only to replace' : '••••••••••••'}
                  className="text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-slate-50"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Sender ID (Optional)</label>
                <input
                  type="text"
                  value={mmSender}
                  onChange={(e) => setMmSender(e.target.value)}
                  placeholder="e.g. 0412345678 or Brand"
                  className="text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-slate-50"
                />
              </div>
            </div>

            {/* Inbound Webhook setup instructions */}
            <div className="p-3 bg-indigo-50/60 border border-indigo-150 rounded-xl flex flex-col gap-1.5">
              <div className="flex justify-between items-center">
                <span className="font-bold text-indigo-900 text-[11px]">Inbound SMS Webhook URL for MobileMessage</span>
                <button
                  onClick={copyWebhookUrl}
                  className="flex items-center gap-1 text-[10px] font-bold text-indigo-700 hover:text-indigo-900 bg-white px-2 py-1 rounded border border-indigo-200 shadow-2xs transition-colors cursor-pointer"
                >
                  {copiedWebhook ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
                  {copiedWebhook ? 'Copied!' : 'Copy Webhook URL'}
                </button>
              </div>
              <code className="text-[11px] font-mono text-indigo-800 bg-white p-2 rounded border border-indigo-200 select-all break-all">
                {webhookUrl}
              </code>
              <p className="text-[10px] text-indigo-700/80">
                Enter this Webhook URL in your <a href="https://app.mobilemessage.com.au/" target="_blank" rel="noreferrer" className="underline font-semibold">MobileMessage Dashboard</a> under <strong>Inbound Webhooks</strong> so inbound customer text messages automatically flow into this triage app.
              </p>
              {window.location.hostname === 'localhost' && (
                <div className="text-[9px] text-amber-700 bg-amber-50 border border-amber-200 p-2 rounded-lg font-semibold mt-1 font-sans">
                  ⚠️ Note: Since you are running locally, this address is a local address. For live SMS routing, make sure to configure your MobileMessage dashboard with your live Fly.io webhook URL: <strong className="font-mono">https://assistant-ui-hub.fly.dev/webhooks/sms</strong>
                </div>
              )}
            </div>

          </div>
        </div>

        {/* Custom Q&A Rules Card */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <MessageSquare className="w-5 h-5 text-indigo-600" />
              <h2 className="font-bold text-slate-800 text-sm">Custom Q&A Rules Engine</h2>
            </div>
            <span className="text-[10px] bg-indigo-100 text-indigo-800 font-extrabold px-2 py-0.5 rounded-full">
              {qaRules.length} Active Rules
            </span>
          </div>

          <div className="p-5 flex flex-col gap-4 font-sans">
            <p className="text-[10px] text-slate-500 -mt-1 leading-normal">
              Define trigger phrases to automatically catch user queries and return exact, static replies immediately (bypassing OpenAI).
            </p>

            {/* List of current Q&A rules */}
            <div className="border border-slate-150 rounded-xl divide-y divide-slate-150 overflow-hidden bg-slate-50/30">
              {qaRules.length === 0 ? (
                <div className="p-6 text-center text-xs text-slate-400 font-medium">
                  No Q&A rules defined yet. Create your first rule below.
                </div>
              ) : (
                qaRules.map((rule) => (
                  <div key={rule.id} className="p-3 flex justify-between items-start gap-4 hover:bg-slate-50 transition-colors">
                    <div className="flex flex-col gap-1">
                      <span className="font-bold text-slate-800 text-xs flex items-center gap-1.5">
                        🔍 Trigger: <span className="font-mono bg-slate-200 text-slate-700 px-1.5 py-0.5 rounded text-[10px]">{rule.trigger}</span>
                      </span>
                      <p className="text-[10px] text-slate-500 leading-relaxed font-sans">{rule.reply}</p>
                    </div>
                    <button
                      onClick={() => handleDeleteQaRule(rule.id)}
                      className="p-1 hover:bg-rose-100 rounded text-rose-600 transition-colors cursor-pointer shrink-0"
                      title="Delete rule"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))
              )}
            </div>

            {/* Add QA Rule form */}
            <form onSubmit={handleAddQaRule} className="p-4 bg-slate-50 border border-slate-200 rounded-xl flex flex-col gap-3">
              <h4 className="font-bold text-slate-700 text-xs flex items-center gap-1">
                <Plus className="w-3.5 h-3.5 text-slate-400" />
                Add New Q&A Rule
              </h4>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Trigger Phrase (Case-Insensitive)</label>
                <input
                  type="text"
                  value={newRuleTrigger}
                  onChange={(e) => setNewRuleTrigger(e.target.value)}
                  placeholder="e.g. pricing, massage link, address"
                  className="text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
                  required
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Static Reply Text</label>
                <textarea
                  value={newRuleReply}
                  onChange={(e) => setNewRuleReply(e.target.value)}
                  placeholder="Type the exact reply Tori should send immediately when this trigger phrase is detected..."
                  rows={2}
                  className="text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 bg-white"
                  required
                />
              </div>
              <div className="flex justify-end">
                <button
                  type="submit"
                  className="bg-slate-800 hover:bg-slate-700 text-white font-bold text-[11px] px-3.5 py-1.5 rounded-lg shadow-sm transition-all cursor-pointer border border-transparent"
                >
                  Add Rule to List
                </button>
              </div>
            </form>

            {/* Save QA Rules control */}
            <div className="flex justify-end pt-2 border-t border-slate-150">
              <button
                onClick={handleSaveQaRules}
                disabled={savingQaRules}
                className="bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs px-4 py-2.5 rounded-lg shadow-sm transition-all cursor-pointer border border-transparent"
              >
                {savingQaRules ? 'Saving Rules list...' : 'Save Q&A Rules'}
              </button>
            </div>

          </div>
        </div>

        {/* First Contact Auto-Responder Card */}
        <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
          <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center gap-3">
            <div className="flex items-center gap-2">
              <MessageSquare className="w-5 h-5 text-emerald-600" />
              <div>
                <h2 className="font-bold text-slate-800 text-sm">First Contact Auto-Responders</h2>
                <p className="text-[10px] text-slate-500">Each SMS number sends its own fixed greeting before normal AI replies begin.</p>
              </div>
            </div>
          </div>

          <div className="p-5 flex flex-col gap-5 font-sans">
            {(['primary', 'secondary'] as const).map((key) => {
              const account = firstContactConfig.accounts[key];
              const label = firstContactConfig.labels[key];
              return (
                <section key={key} className="rounded-xl border border-slate-200 bg-slate-50/40 p-4">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <div>
                      <h3 className="text-sm font-extrabold text-slate-800">{label}</h3>
                      <p className="text-[10px] text-slate-500">{key === 'primary' ? 'Original SMS account · Line 1' : 'Second SMS account · Line 2'}</p>
                    </div>
                    <button
                      type="button"
                      role="switch"
                      aria-label={`Enable ${label} first-contact reply`}
                      aria-checked={account.enabled}
                      onClick={() => updateFirstContactAccount(key, { enabled: !account.enabled })}
                      className={`relative h-6 w-11 shrink-0 rounded-full border-none p-0 transition-colors cursor-pointer ${account.enabled ? 'bg-emerald-500' : 'bg-slate-300'}`}
                    >
                      <span className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${account.enabled ? 'translate-x-5' : 'translate-x-0'}`} />
                    </button>
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-[180px_180px_1fr] gap-4">
                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Quiet Period</label>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min={1}
                          max={3650}
                          value={account.cooldownDays}
                          onChange={(event) => updateFirstContactAccount(key, { cooldownDays: Number(event.target.value) })}
                          className="w-24 text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-white"
                        />
                        <span className="text-xs font-semibold text-slate-600">days</span>
                      </div>
                      <p className="text-[10px] text-slate-400">Send again only after this many quiet days on this SMS line.</p>
                    </div>

                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Response Delay</label>
                      <div className="flex items-center gap-2">
                        <input
                          type="number"
                          min={0}
                          max={3600}
                          value={account.delaySeconds}
                          onChange={(event) => updateFirstContactAccount(key, { delaySeconds: Number(event.target.value) })}
                          className="w-24 text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-white"
                        />
                        <span className="text-xs font-semibold text-slate-600">seconds</span>
                      </div>
                      <p className="text-[10px] text-slate-400">Wait before sending this line's greeting.</p>
                    </div>

                    <div className="flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">{label} Fixed Reply</label>
                      <textarea
                        value={account.message}
                        onChange={(event) => updateFirstContactAccount(key, { message: event.target.value })}
                        rows={4}
                        maxLength={1600}
                        placeholder={`Type the exact first reply for ${label}...`}
                        className="text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-white"
                      />
                      <p className="text-[10px] text-slate-400">Sent instead of an AI reply for the first message on this line.</p>
                    </div>
                  </div>
                </section>
              );
            })}

            <div className="flex justify-end border-t border-slate-200 pt-4">
              <button
                type="button"
                onClick={handleSaveFirstContact}
                disabled={savingFirstContact}
                className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 text-white font-bold text-xs px-4 py-2.5 rounded-lg shadow-sm transition-all cursor-pointer border border-transparent"
              >
                {savingFirstContact ? 'Saving...' : 'Save Both First Contact Replies'}
              </button>
            </div>
          </div>
        </div>

        {/* Document Editor & Moderation Modal */}
        {activeEditFile && (
          <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div className="bg-white rounded-xl shadow-xl border border-slate-200 w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden animate-in fade-in zoom-in-95 duration-150">
              
              {/* Modal Header */}
              <div className="p-4 border-b border-slate-200 bg-slate-50 flex justify-between items-center">
                <div>
                  <h3 className="font-bold text-slate-900 text-sm flex items-center gap-2">
                    <FileText className="w-4 h-4 text-indigo-600" />
                    Moderate Document - {activeEditFile}
                  </h3>
                  <p className="text-[10px] text-slate-500 mt-0.5 font-sans">Edit raw content or weed out personal/non-client items.</p>
                </div>
                <button
                  onClick={() => setActiveEditFile(null)}
                  className="p-1 hover:bg-slate-255 rounded text-slate-400 hover:text-slate-600 transition-colors cursor-pointer"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              {/* Modal Tabs */}
              <div className="flex border-b border-slate-150 px-4 bg-slate-50/50">
                <button
                  onClick={() => setModalTab('editor')}
                  className={`px-4 py-2.5 text-xs font-bold border-b-2 font-sans transition-all cursor-pointer ${
                    modalTab === 'editor'
                      ? 'border-indigo-600 text-indigo-600 font-sans'
                      : 'border-transparent text-slate-600 hover:text-slate-900'
                  }`}
                >
                  File Editor
                </button>
                {activeEditFile.endsWith('.jsonl') && (
                  <button
                    onClick={() => setModalTab('moderator')}
                    className={`px-4 py-2.5 text-xs font-bold border-b-2 font-sans transition-all cursor-pointer ${
                      modalTab === 'moderator'
                        ? 'border-indigo-600 text-indigo-600 font-sans'
                        : 'border-transparent text-slate-600 hover:text-slate-900'
                    }`}
                  >
                    Search & Weed (Moderator)
                  </button>
                )}
              </div>

              {/* Modal Body */}
              <div className="flex-1 overflow-y-auto p-5">
                {modalTab === 'editor' ? (
                  <div className="flex flex-col gap-3 h-full">
                    {/* Large file informational alert */}
                    {activeEditFileSize >= 500 * 1024 && (
                      <div className="p-4 bg-sky-50 border border-sky-200 text-sky-900 rounded-lg flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 text-xs font-sans">
                        <div className="flex gap-2.5">
                          <BookOpen className="w-5 h-5 text-sky-600 shrink-0 mt-0.5" />
                          <div>
                            <p className="font-bold text-sky-950">Large Dataset Preview ({formatSize(activeEditFileSize)})</p>
                            <p className="text-[10px] text-sky-700 mt-0.5">To prevent your browser from freezing, we show a read-only preview of the first 100 rows. You can moderate the file using the <strong>Search & Weed (Moderator)</strong> tab above, or download it to edit locally.</p>
                          </div>
                        </div>
                        <button
                          onClick={handleDownloadFullFile}
                          className="bg-sky-600 hover:bg-sky-700 text-white font-bold text-xs px-3.5 py-2 rounded-lg shadow-sm transition-all cursor-pointer border border-transparent shrink-0 flex items-center gap-1.5 font-sans"
                        >
                          <UploadCloud className="w-3.5 h-3.5 rotate-180" />
                          Download File
                        </button>
                      </div>
                    )}

                    <div className="flex-1 flex flex-col gap-1.5">
                      <label className="text-[10px] font-bold text-slate-500 uppercase tracking-wider font-sans">
                        {activeEditFileSize >= 500 * 1024 ? 'First 100 rows (read-only)' : 'File content'}
                      </label>
                      {loadingFileContent ? (
                        <div className="py-20 text-center text-xs text-slate-400 font-sans">
                          Loading file content...
                        </div>
                      ) : (
                        <textarea
                          value={editorContent}
                          onChange={(e) => setEditorContent(e.target.value)}
                          readOnly={activeEditFileSize >= 500 * 1024}
                          rows={14}
                          className="w-full font-mono text-[11px] bg-slate-900 text-slate-100 p-3 rounded-lg border border-slate-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 h-96 resize-none"
                        />
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="flex flex-col gap-4 font-sans">
                    {/* Weeding controls info */}
                    <div className="p-3 bg-slate-50 border border-slate-200 text-slate-700 rounded-lg text-xs flex gap-2">
                      <BookOpen className="w-4 h-4 text-indigo-650 shrink-0" />
                      <div>
                        <p className="font-bold">Training Data Moderation</p>
                        <p className="text-[10px] text-slate-500 mt-0.5">Use this search utility to weed out personal conversations (e.g. chats mentioning family, friends, bank transfers, or unrelated topics) from the training examples.</p>
                      </div>
                    </div>

                    {/* Blacklist / Bulk actions */}
                    <div className="flex flex-wrap gap-2 items-center justify-between p-3 bg-indigo-50/50 border border-indigo-100 rounded-lg text-xs">
                      <div>
                        <p className="font-bold text-indigo-950">Bulk Pruning Utilities</p>
                        <p className="text-[10px] text-indigo-700">Quickly wipe accounts or generic private details.</p>
                      </div>
                      <button
                        onClick={handleBlacklistPurge}
                        className="bg-indigo-600 hover:bg-indigo-705 text-white text-xs font-bold px-3 py-1.5 rounded-lg shadow-sm transition-all cursor-pointer border border-transparent font-sans"
                      >
                        Clean Common Blacklist
                      </button>
                    </div>

                    {/* Search row inputs */}
                    <div className="flex gap-2">
                      <div className="flex-1 relative">
                        <input
                          type="text"
                          value={modQuery}
                          onChange={(e) => setModQuery(e.target.value)}
                          onKeyDown={(e) => e.key === 'Enter' && handleSearchMod()}
                          placeholder="Search keywords, phone numbers, names..."
                          className="w-full text-xs border border-slate-350 rounded-lg pl-9 pr-3 py-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 font-sans"
                        />
                        <Search className="w-4 h-4 text-slate-400 absolute left-3 top-3" />
                      </div>
                      <button
                        onClick={handleSearchMod}
                        disabled={searchingMod || !modQuery.trim()}
                        className="bg-slate-905 hover:bg-slate-800 disabled:opacity-50 text-white text-xs font-bold px-4 py-2 rounded-lg shadow-sm transition-all cursor-pointer border border-transparent font-sans"
                      >
                        {searchingMod ? 'Searching...' : 'Search'}
                      </button>
                    </div>

                    {/* Results table */}
                    <div className="flex flex-col gap-2">
                      {modResults.length > 0 && (
                        <div className="flex justify-between items-center text-xs">
                          <span className="font-bold text-slate-700">
                            Found {totalMatches} matches {totalMatches > 100 && '(showing first 100)'}
                          </span>
                          <button
                            onClick={() => handleBulkPurgeQuery(modQuery)}
                            className="text-xs font-bold text-rose-650 hover:text-rose-800 hover:underline flex items-center gap-1 cursor-pointer font-sans"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                            Delete All Matching Rows
                          </button>
                        </div>
                      )}

                      <div className="border border-slate-200 rounded-lg overflow-hidden max-h-[300px] overflow-y-auto bg-slate-50">
                        {modResults.length === 0 ? (
                          <div className="py-12 text-center text-xs text-slate-400 font-sans">
                            {searchingMod ? 'Retrieving search results...' : 'Enter a search term above to moderate rows.'}
                          </div>
                        ) : (
                          <table className="w-full text-xs font-sans border-collapse">
                            <thead>
                              <tr className="bg-slate-200 border-b border-slate-300 text-left font-bold text-slate-700">
                                <th className="p-2 w-10">Row</th>
                                <th className="p-2 w-1/2">Input</th>
                                <th className="p-2 w-1/2">Output</th>
                                <th className="p-2 text-center w-12">Action</th>
                              </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-200">
                              {modResults.map((item) => (
                                <tr key={item.index} className="hover:bg-slate-100 transition-colors">
                                  <td className="p-2 text-slate-500 font-semibold">{item.index + 1}</td>
                                  <td className="p-2 font-mono text-[10px] break-words text-slate-800">{item.input}</td>
                                  <td className="p-2 font-mono text-[10px] break-words text-indigo-900">{item.output}</td>
                                  <td className="p-2 text-center">
                                    <button
                                      onClick={() => handlePurgeIndex(item.index)}
                                      className="p-1 hover:bg-rose-100 rounded text-rose-600 transition-colors cursor-pointer"
                                      title="Purge row from file"
                                    >
                                      <Trash2 className="w-3.5 h-3.5" />
                                    </button>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        )}
                      </div>
                    </div>
                  </div>
                )}
              </div>

              {/* Modal Footer */}
              <div className="p-4 border-t border-slate-200 bg-slate-50 flex justify-end gap-2">
                <button
                  onClick={() => setActiveEditFile(null)}
                  className="bg-white border border-slate-350 hover:bg-slate-50 text-slate-700 text-xs font-semibold px-4 py-2 rounded-lg shadow-sm transition-all cursor-pointer font-sans"
                >
                  Close
                </button>
                {modalTab === 'editor' && activeEditFileSize < 500 * 1024 && (
                  <button
                    onClick={handleSaveFileContent}
                    disabled={savingFileContent || loadingFileContent}
                    className="bg-indigo-600 hover:bg-indigo-705 disabled:opacity-50 text-white text-xs font-bold px-4 py-2 rounded-lg shadow-sm transition-all cursor-pointer border border-transparent font-sans"
                  >
                    {savingFileContent ? 'Saving Content...' : 'Save File Content'}
                  </button>
                )}
              </div>

            </div>
          </div>
        )}
      </div>
    </div>
  );
}


