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
  clearPendingDrafts
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
  const [savingServices, setSavingServices] = useState(false);
  const [savingSmsTemplate, setSavingSmsTemplate] = useState(false);


  // New service form state
  const [newServiceName, setNewServiceName] = useState('');
  const [newServiceDesc, setNewServiceDesc] = useState('');
  const [newServicePrice, setNewServicePrice] = useState(100);
  const [newServiceDuration, setNewServiceDuration] = useState(60);
  const [newServiceShowDuration, setNewServiceShowDuration] = useState(true);

  // Edit service form state
  const [editingServiceId, setEditingServiceId] = useState<string | null>(null);
  const [editServiceName, setEditServiceName] = useState('');
  const [editServiceDesc, setEditServiceDesc] = useState('');
  const [editServicePrice, setEditServicePrice] = useState(100);
  const [editServiceDuration, setEditServiceDuration] = useState(60);
  const [editServiceShowDuration, setEditServiceShowDuration] = useState(true);


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
  const [firstContactConfig, setFirstContactConfig] = useState<FirstContactAutoresponderConfig>({
    enabled: false,
    cooldownDays: 30,
    delaySeconds: 0,
    message: ''
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

  const handleAddService = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newServiceName.trim()) return;
    const newService: Service = {
      id: `srv_${Date.now()}`,
      name: newServiceName,
      description: newServiceDesc,
      price: newServicePrice,
      duration: newServiceDuration,
      showDuration: newServiceShowDuration
    };
    setServices([...services, newService]);
    setNewServiceName('');
    setNewServiceDesc('');
    setNewServicePrice(100);
    setNewServiceDuration(60);
    setNewServiceShowDuration(true);
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
          showDuration: editServiceShowDuration
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
    if (firstContactConfig.enabled && !firstContactConfig.message.trim()) {
      triggerBanner('error', 'Enter a first-contact reply message before enabling it.');
      return;
    }
    setSavingFirstContact(true);
    try {
      const config = {
        ...firstContactConfig,
        cooldownDays: Math.max(1, Math.min(3650, Number(firstContactConfig.cooldownDays) || 1)),
        delaySeconds: Math.max(0, Math.min(3600, Number(firstContactConfig.delaySeconds) || 0)),
        message: firstContactConfig.message.trim()
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

  const widgetScriptUrl = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'https://assistant-ui-hub.fly.dev/widget.js'
    : `${window.location.origin}/widget.js`;
  const widgetBaseUrl = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    ? 'https://assistant-ui-hub.fly.dev'
    : window.location.origin;

  const embedScriptCode = `<div id="booking-container"></div>\n<script src="${widgetScriptUrl}"></script>\n<script>\nnew SimplybookWidget({\n  "container_id": "booking-container",\n  "widget_type": "iframe",\n  "url": "${widgetBaseUrl}"\n});\n</script>`;

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
            <a href="https://app.mobilemessage.com.au/" target="_blank" rel="noopener noreferrer" className="text-indigo-600 hover:text-indigo-850 hover:underline break-all font-semibold">
              https://app.mobilemessage.com.au/
            </a>
          </div>
        </div>

        {/* Banner Alert */}
        {banner && (
          <div
            className={`p-3.5 rounded-lg border text-xs font-semibold flex items-center gap-2.5 shadow-sm transition-all duration-355 ${
              banner.type === 'success'
                ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
                : 'bg-rose-50 text-rose-800 border-rose-200'
            }`}
          >
            {banner.type === 'success' ? (
              <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
            ) : (
              <AlertCircle className="w-4 h-4 text-rose-600 shrink-0" />
            )}
            <span>{banner.message}</span>
          </div>
        )}

        {loadingSettings && (
          <div className="rounded-lg border border-indigo-100 bg-indigo-50 px-3 py-2 text-[10px] font-semibold text-indigo-700 flex items-center gap-2">
            <RefreshCw className="w-3.5 h-3.5 animate-spin" /> Refreshing saved settings...
          </div>
        )}
        {settingsConnectionError && (
          <div className="rounded-lg border border-rose-200 bg-rose-50 px-4 py-3 text-xs text-rose-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-start gap-2">
              <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
              <span><strong>Production settings are temporarily unavailable in this page.</strong> Saved values have not been erased. Retry before editing or saving.</span>
            </div>
            <button
              type="button"
              onClick={loadAllSettings}
              className="rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-[10px] font-bold text-rose-800 hover:bg-rose-100 cursor-pointer shrink-0"
            >
              Retry connection
            </button>
          </div>
        )}
        <div className="grid grid-cols-1 gap-6">
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
              <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center gap-3">
                <div className="w-9 h-9 rounded-lg bg-indigo-50 text-indigo-600 flex items-center justify-center shrink-0">
                  <BellRing className="w-4.5 h-4.5" />
                </div>
                <div>
                  <h2 className="font-bold text-slate-800 text-sm">Device Alerts &amp; Display</h2>
                  <p className="text-[10px] text-slate-500 mt-0.5">Choose how this portal behaves and sounds.</p>
                </div>
              </div>

              <div className="p-4 flex items-center justify-between gap-4 border-b border-slate-100">
                <div className="flex items-center gap-3 min-w-0">
                  <MessageSquare className="w-4 h-4 text-slate-500 shrink-0" />
                  <div className="min-w-0">
                    <p className="text-xs font-bold text-slate-800">Message avatars</p>
                    <p className="text-[10px] text-slate-500 mt-0.5">Show the circular avatar beside each conversation.</p>
                  </div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={showMessageAvatars}
                  aria-label="Show message avatars"
                  disabled={savingMessageDisplay}
                  onClick={handleMessageAvatarToggle}
                  className={`relative h-6 w-11 shrink-0 rounded-full border-none p-0 transition-colors cursor-pointer disabled:opacity-50 ${showMessageAvatars ? 'bg-indigo-600' : 'bg-slate-300'}`}
                >
                  <span className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${showMessageAvatars ? 'translate-x-5' : 'translate-x-0'}`} />
                </button>
              </div>

              <div className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-100">
                <div className="flex items-start gap-3 min-w-0">
                  <Trash2 className="w-4 h-4 text-rose-600 mt-0.5 shrink-0" />
                  <div className="min-w-0">
                    <p className="text-xs font-bold text-slate-800">Clear pending AI drafts</p>
                    <p className="text-[10px] text-slate-500 mt-0.5">Remove every unsent AI reply currently waiting for approval.</p>
                  </div>
                </div>
                <button
                  type="button"
                  disabled={clearingPendingDrafts}
                  onClick={handleClearPendingDrafts}
                  className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-rose-200 bg-white px-3 py-2 text-[10px] font-bold text-rose-700 hover:bg-rose-50 disabled:cursor-not-allowed disabled:opacity-50 cursor-pointer shrink-0"
                >
                  {clearingPendingDrafts ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                  {clearingPendingDrafts ? 'Clearing…' : 'Clear all drafts'}
                </button>
              </div>

              <div className="p-4 space-y-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3 min-w-0">
                    <BellRing className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <p className="text-xs font-bold text-slate-800">Customer arrival air-raid siren</p>
                      <p className="text-[10px] text-slate-500 mt-0.5">Sounds only when the customer says they have arrived. This setting applies only to this browser on this device.</p>
                    </div>
                  </div>
                  <button
                    type="button"
                    role="switch"
                    aria-checked={incomingAlarmEnabled}
                    aria-label="Customer arrival siren on this device"
                    onClick={handleIncomingAlarmToggle}
                    className={`relative h-6 w-11 shrink-0 rounded-full border-none p-0 transition-colors cursor-pointer ${incomingAlarmEnabled ? 'bg-amber-500' : 'bg-slate-300'}`}
                  >
                    <span className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${incomingAlarmEnabled ? 'translate-x-5' : 'translate-x-0'}`} />
                  </button>
                </div>

                <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:pl-7">
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <Volume2 className="w-4 h-4 text-slate-500 shrink-0" />
                    <input
                      type="range"
                      min="0"
                      max="100"
                      value={incomingAlarmVolume}
                      onChange={(event) => handleIncomingAlarmVolume(Number(event.target.value))}
                      aria-label="Customer arrival siren volume"
                      className="w-full accent-amber-500"
                    />
                    <span className="text-[10px] font-bold text-slate-600 w-9 text-right">{incomingAlarmVolume}%</span>
                  </div>
                  <button
                    type="button"
                    onClick={handleTestIncomingAlarm}
                    className={`px-3 py-2 rounded-lg text-xs font-bold cursor-pointer border ${testingIncomingAlarm ? 'bg-red-50 text-red-700 border-red-200' : 'bg-amber-50 text-amber-800 border-amber-200 hover:bg-amber-100'}`}
                  >
                    {testingIncomingAlarm ? 'Stop Siren' : 'Test Siren'}
                  </button>
                </div>
                <p className="text-[10px] text-slate-500 sm:pl-7">The device's physical volume and browser tab sound permission still control the maximum loudness.</p>
              </div>
            </div>
            
            {/* Business Variables Section */}
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
              <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <BookOpen className="w-5 h-5 text-indigo-600" />
                  <div>
                    <h2 className="font-bold text-slate-800 text-sm">Business Variables</h2>
                    <p className="text-[10px] text-slate-500 mt-0.5">See every available token here, then open Manage Variables only when you need to edit values.</p>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setShowVariableEditor((current) => !current)}
                  className="flex items-center gap-1.5 bg-white hover:bg-slate-50 text-indigo-700 border border-indigo-200 text-xs px-3 py-2 rounded-lg font-bold cursor-pointer shrink-0"
                >
                  {showVariableEditor ? <X className="w-3.5 h-3.5" /> : <Edit className="w-3.5 h-3.5" />}
                  {showVariableEditor ? 'Close Editor' : 'Manage Variables'}
                </button>
              </div>
              <div className="p-5 flex flex-col gap-4">
                <div className="rounded-lg border border-indigo-100 bg-indigo-50/60 p-3 text-[10px] leading-relaxed text-indigo-900">
                  Non-empty business values are supplied to the AI automatically. Click any token below to copy it for a prompt or confirmation template.
                </div>

                <div>
                  <h3 className="text-[10px] font-bold uppercase tracking-wide text-slate-500 mb-2">Built-in application tokens</h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {BUILT_IN_TEMPLATE_VARIABLES.map((variable) => (
                      <button
                        key={variable.key}
                        type="button"
                        onClick={() => copyVariableToken(variable.key)}
                        className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-left hover:border-indigo-300 hover:bg-indigo-50 cursor-pointer"
                      >
                        <span className="min-w-0">
                          <span className="block text-[11px] font-semibold text-slate-700">{variable.label}</span>
                          <span className="block text-[9px] text-slate-400">{variable.scope}</span>
                        </span>
                        <code className="text-[10px] font-bold text-indigo-700 shrink-0">{copiedVariableToken === variable.key ? 'Copied' : `{${variable.key}}`}</code>
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <h3 className="text-[10px] font-bold uppercase tracking-wide text-slate-500 mb-2">Business detail tokens</h3>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {businessVariables.map((variable) => (
                      <button
                        key={variable.key}
                        type="button"
                        onClick={() => copyVariableToken(variable.key)}
                        className="flex items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 text-left hover:border-indigo-300 hover:bg-indigo-50 cursor-pointer"
                      >
                        <span className="flex items-center gap-2 min-w-0">
                          <span className={`h-2 w-2 rounded-full shrink-0 ${variable.value.trim() ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                          <span className="min-w-0">
                            <span className="flex items-center gap-1.5">
                              <span className="block truncate text-[11px] font-semibold text-slate-700">{variable.label}</span>
                              <span className="text-[9px] font-mono text-slate-400">({variable.key})</span>
                            </span>
                            <span className="block text-[9px] text-slate-400 truncate">{variable.description || (variable.value.trim() ? 'Value saved' : 'No value set')}</span>
                          </span>
                        </span>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <span className={`text-[8px] font-bold uppercase px-1.5 py-0.5 rounded ${variable.required || variable.required_status === 'required' ? 'bg-amber-100 text-amber-800 border border-amber-200' : 'bg-slate-100 text-slate-600 border border-slate-200'}`}>
                            {variable.required || variable.required_status === 'required' ? 'Required' : 'Optional'}
                          </span>
                          <code className="text-[10px] font-bold text-indigo-700">{copiedVariableToken === variable.key ? 'Copied' : (variable.token || `{${variable.key}}`)}</code>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>

                {showVariableEditor && (
                  <div className="mt-1 rounded-xl border border-indigo-200 bg-slate-50 p-3 sm:p-4 flex flex-col gap-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <h3 className="text-xs font-bold text-slate-800">Manage business details</h3>
                        <p className="text-[9px] text-slate-500 mt-0.5">Changes take effect after Save Variables.</p>
                      </div>
                      <button
                        type="button"
                        onClick={addBusinessVariable}
                        className="flex items-center gap-1 rounded-lg border border-indigo-200 bg-white px-2.5 py-1.5 text-[10px] font-bold text-indigo-700 hover:bg-indigo-50 cursor-pointer"
                      >
                        <Plus className="w-3 h-3" /> Add custom
                      </button>
                    </div>
                    {businessVariables.map((variable, index) => (
                      <div key={`${variable.key}-${index}`} className="grid grid-cols-1 lg:grid-cols-[minmax(140px,0.8fr)_minmax(150px,0.9fr)_minmax(220px,1.6fr)_32px] gap-2 items-start rounded-lg border border-slate-200 bg-white p-2.5">
                        <input
                          aria-label="Variable label"
                          value={variable.label}
                          onChange={(event) => updateBusinessVariable(index, 'label', event.target.value)}
                          className="w-full text-xs border border-slate-300 rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          placeholder="Display label"
                        />
                        <div className="flex items-center rounded-lg border border-slate-300 bg-white focus-within:ring-2 focus-within:ring-indigo-500">
                          <span className="pl-2 text-xs font-mono text-slate-400">{'{'}</span>
                          <input
                            aria-label="Variable token"
                            value={variable.key}
                            onChange={(event) => updateBusinessVariable(index, 'key', event.target.value)}
                            className="min-w-0 flex-1 border-0 bg-transparent py-2 text-xs font-mono focus:outline-none"
                            placeholder="variable_name"
                          />
                          <span className="pr-2 text-xs font-mono text-slate-400">{'}'}</span>
                        </div>
                        <input
                          aria-label="Variable value"
                          value={variable.value}
                          onChange={(event) => updateBusinessVariable(index, 'value', event.target.value)}
                          className="w-full text-xs border border-slate-300 rounded-lg p-2 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                          placeholder="Business value"
                        />
                        <button
                          type="button"
                          onClick={() => removeBusinessVariable(index)}
                          title={`Remove ${variable.label || variable.key}`}
                          className="h-8 w-8 flex items-center justify-center rounded-lg text-slate-500 hover:text-rose-600 hover:bg-rose-50 cursor-pointer"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                    <div className="pt-2 border-t border-slate-200 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                      <p className="text-[9px] text-slate-500">Keys use lowercase letters, numbers, and underscores.</p>
                      <button
                        type="button"
                        onClick={handleSaveBusinessVariables}
                        disabled={savingBusinessVariables}
                        className="bg-indigo-600 hover:bg-indigo-700 text-white text-xs px-4 py-2 rounded-lg font-bold cursor-pointer disabled:opacity-50"
                      >
                        {savingBusinessVariables ? 'Saving...' : 'Save Variables'}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* OpenAI Configuration Section */}
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
              <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center gap-2">
                <Cpu className="w-5 h-5 text-indigo-600" />
                <h2 className="font-bold text-slate-800 text-sm">OpenAI Agent & Prompt Configuration</h2>
              </div>
              <form onSubmit={handleSaveSettings} className="p-5 flex flex-col gap-4">
                
                {/* API Key */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-bold text-slate-700 flex items-center gap-1.5">
                    <Key className="w-3.5 h-3.5 text-slate-400" /> OpenAI API Key
                  </label>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="sk-..."
                    className="w-full text-xs bg-slate-50 border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:bg-white"
                  />
                  <p className="text-[10px] text-slate-400">Values are masked by default. Updates write to backend .env variables.</p>
                </div>


                {/* System Prompt */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-bold text-slate-700 flex items-center gap-1.5">
                    <Terminal className="w-3.5 h-3.5 text-slate-400" /> System Instruction Prompt
                  </label>
                  <textarea
                    value={systemPrompt}
                    onChange={(e) => setSystemPrompt(e.target.value)}
                    rows={8}
                    className="w-full text-xs font-mono bg-slate-900 text-slate-200 border border-slate-805 rounded-lg p-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                  <p className="text-[10px] text-slate-400">Defines constraints, timezone rules, persona, and response formatting. Business variable tokens can be used here.</p>
                </div>

                {/* User Prompt */}
                <div className="flex flex-col gap-1.5">
                  <label className="text-xs font-bold text-slate-700 flex items-center gap-1.5">
                    <FileText className="w-3.5 h-3.5 text-slate-400" /> User Template Prompt
                  </label>
                  <textarea
                    value={userPrompt}
                    onChange={(e) => setUserPrompt(e.target.value)}
                    rows={4}
                    className="w-full text-xs font-mono bg-slate-900 text-slate-200 border border-slate-805 rounded-lg p-3 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                  <p className="text-[10px] text-slate-400">Keep the live injection tokens <code className="bg-slate-200 text-slate-750 px-1 rounded font-bold">{"{message}"}</code>, <code className="bg-slate-200 text-slate-750 px-1 rounded font-bold">{"{knowledge}"}</code>, and <code className="bg-slate-200 text-slate-750 px-1 rounded font-bold">{"{slots}"}</code>. Business variable tokens are also supported.</p>
                </div>

                <div className="pt-2 border-t border-slate-100 flex justify-end">
                  <button
                    type="submit"
                    disabled={savingSettings}
                    className="bg-indigo-600 hover:bg-indigo-700 text-white text-xs px-4 py-2 rounded-lg font-bold shadow-sm transition-all cursor-pointer disabled:opacity-50 border border-transparent font-sans"
                  >
                    {savingSettings ? 'Saving Configurations...' : 'Save Configurations'}
                  </button>
                </div>

              </form>
            </div>

            {/* Local RAG Section */}
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
              <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center gap-2">
                <FileText className="w-5 h-5 text-indigo-600" />
                <div>
                  <h2 className="font-bold text-slate-800 text-sm">Learned Material &amp; Knowledge Documents</h2>
                  <p className="text-[10px] text-slate-500 mt-0.5">Teach a situation directly or manage the underlying Local RAG files.</p>
                </div>
              </div>
              <div className="p-5 flex flex-col gap-5">

                <form onSubmit={handleCreateLearning} className="rounded-xl border border-indigo-200 bg-indigo-50/50 p-4 flex flex-col gap-3">
                  <div className="flex items-start gap-2.5">
                    <div className="mt-0.5 h-8 w-8 rounded-lg bg-indigo-100 text-indigo-700 flex items-center justify-center shrink-0">
                      <BookOpen className="w-4 h-4" />
                    </div>
                    <div>
                      <h3 className="text-xs font-bold text-indigo-950">Add learned guidance</h3>
                      <p className="text-[10px] leading-relaxed text-indigo-800 mt-0.5">
                        Write rough notes in your own words. The AI will turn them into a reusable instruction and only add an example reply when your notes actually provide wording.
                      </p>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 gap-3">
                    <div className="flex flex-col gap-1.5">
                      <label htmlFor="learning-topic" className="text-[10px] font-bold uppercase tracking-wider text-slate-600">Topic or situation</label>
                      <input
                        id="learning-topic"
                        value={learningTopic}
                        onChange={(event) => setLearningTopic(event.target.value)}
                        maxLength={500}
                        placeholder="e.g. Customer asks to change their existing booking time"
                        className="w-full rounded-lg border border-slate-300 bg-white p-2.5 text-xs text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      />
                    </div>
                    <div className="flex flex-col gap-1.5">
                      <label htmlFor="learning-guidance" className="text-[10px] font-bold uppercase tracking-wider text-slate-600">What should the AI do or say?</label>
                      <textarea
                        id="learning-guidance"
                        value={learningGuidance}
                        onChange={(event) => setLearningGuidance(event.target.value)}
                        rows={5}
                        maxLength={6000}
                        placeholder="Describe the action, rule, or suggested wording. It can be messy; the AI will structure it without adding new facts."
                        className="w-full resize-y rounded-lg border border-slate-300 bg-white p-2.5 text-xs leading-relaxed text-slate-800 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                      />
                      <p className="text-[9px] text-slate-500">Procedures and policies are saved as instructions. Exact wording is saved as an example only when you supply it.</p>
                    </div>
                  </div>

                  <div className="flex items-center justify-between gap-3 border-t border-indigo-100 pt-3">
                    <span className="text-[9px] font-semibold text-indigo-700">Nothing is saved if the AI cannot structure it safely.</span>
                    <button
                      type="submit"
                      disabled={savingLearning || !learningTopic.trim() || !learningGuidance.trim()}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-transparent bg-indigo-600 px-4 py-2.5 text-xs font-bold text-white shadow-sm hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-45 cursor-pointer shrink-0"
                    >
                      {savingLearning ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                      {savingLearning ? 'Structuring...' : 'Add to learned material'}
                    </button>
                  </div>

                  {lastSavedLearning && (
                    <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-[10px] text-emerald-950">
                      <div className="flex items-center gap-1.5 font-bold mb-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600" /> Last learning added
                      </div>
                      <p><strong>Topic:</strong> {lastSavedLearning.topic}</p>
                      <p className="mt-1"><strong>Applies when:</strong> {lastSavedLearning.applies_when}</p>
                      <p className="mt-1"><strong>Instruction:</strong> {lastSavedLearning.instruction}</p>
                      {lastSavedLearning.example_reply && (
                        <p className="mt-1"><strong>Example reply:</strong> {lastSavedLearning.example_reply}</p>
                      )}
                    </div>
                  )}
                </form>
                
                {/* List of files */}
                <div>
                  <h3 className="text-xs font-bold text-slate-700 mb-2 font-sans">Files loaded in /backend/knowledge/</h3>
                  <div className="border border-slate-200 rounded-lg divide-y divide-slate-150 overflow-hidden bg-slate-50">
                    {loadingFiles ? (
                      <div className="p-4 text-center text-xs text-slate-400">
                        Retrieving knowledge files...
                      </div>
                    ) : knowledgeFiles.length === 0 ? (
                      <div className="p-4 text-center text-xs text-slate-400">
                        No files uploaded yet. Add FAQs or training sheets.
                      </div>
                    ) : (
                      knowledgeFiles.map((file) => (
                        <div key={file.name} className="p-3 flex justify-between items-center text-xs font-sans hover:bg-slate-100 transition-colors">
                          <span className="font-medium text-slate-700 flex items-center gap-1.5">
                            <File className="w-3.5 h-3.5 text-slate-400" />
                            {file.name}
                            <span className="text-[9px] text-slate-400 font-semibold bg-slate-200 px-1.5 py-0.5 rounded ml-1.5">
                              {formatSize(file.sizeBytes)}
                            </span>
                          </span>
                          <div className="flex items-center gap-1.5">
                            <button
                              onClick={() => handleOpenEdit(file)}
                              title="Edit or moderate document"
                              className="p-1 hover:bg-slate-200 rounded text-slate-600 hover:text-indigo-600 cursor-pointer transition-colors"
                            >
                              <Edit className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={() => handleDeleteFile(file.name)}
                              title="Delete document"
                              className="p-1 hover:bg-rose-100 rounded text-slate-650 hover:text-rose-650 cursor-pointer transition-colors"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>

                {/* Upload knowledge file */}
                <div className="flex flex-col gap-2">
                  <label className="text-xs font-bold text-slate-700 font-sans">Upload Knowledge File (.jsonl, .txt)</label>
                  <div className="border-2 border-dashed border-slate-300 rounded-xl p-6 bg-slate-50 hover:bg-slate-100/50 transition-colors flex flex-col items-center justify-center gap-2 cursor-pointer relative">
                    <input
                      type="file"
                      accept=".txt,.jsonl"
                      onChange={handleKnowledgeUpload}
                      disabled={uploadingKnowledge}
                      className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
                    />
                    <UploadCloud className="w-8 h-8 text-slate-400" />
                    <span className="text-xs font-semibold text-slate-650 font-sans">
                      {uploadingKnowledge ? 'Uploading file...' : 'Click or Drag File Here to Upload'}
                    </span>
                    <span className="text-[9px] text-slate-400 font-sans">Supported formats: Text (.txt) or JSON Lines (.jsonl)</span>
                  </div>
                </div>

              </div>
            </div>

            {/* Google Calendar Section */}
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden">
              <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center gap-2">
                <Calendar className="w-5 h-5 text-indigo-650" />
                <h2 className="font-bold text-slate-800 text-sm">Google Calendar Account Integration</h2>
              </div>
              <div className="p-5 flex flex-col gap-5">
                
                {/* Status indicator */}
                <div className="flex items-center justify-between p-3.5 rounded-lg bg-slate-50 border border-slate-200 text-xs font-sans">
                  <span className="font-semibold text-slate-700">Calendar Bind Mode:</span>
                  {hasGoogleCreds ? (
                    <span className="bg-emerald-100 text-emerald-800 px-3 py-1 rounded-full font-bold flex items-center gap-1.5 border border-emerald-200 text-[10px] font-sans">
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-650" />
                      Active Google Calendar Connection
                    </span>
                  ) : (
                    <span className="bg-slate-200 text-slate-700 px-3 py-1 rounded-full font-bold flex items-center gap-1.5 border border-slate-350 text-[10px] font-sans">
                      <AlertCircle className="w-3.5 h-3.5 text-slate-500" />
                      SQLite Database Fallback Mode
                    </span>
                  )}
                </div>

                {/* Upload credentials uploader */}
                <div className="flex flex-col gap-2">
                  <label className="text-xs font-bold text-slate-700 flex items-center gap-1 font-sans">
                    Upload GCP Service Account JSON key
                  </label>
                  <div className="border-2 border-dashed border-slate-300 rounded-xl p-6 bg-slate-50 hover:bg-slate-100/50 transition-colors flex flex-col items-center justify-center gap-2 cursor-pointer relative">
                    <input
                      type="file"
                      accept=".json"
                      onChange={handleCredentialsUpload}
                      disabled={uploadingCreds}
                      className="absolute inset-0 opacity-0 w-full h-full cursor-pointer"
                    />
                    <UploadCloud className="w-8 h-8 text-slate-400" />
                    <span className="text-xs font-semibold text-slate-650 font-sans">
                      {uploadingCreds ? 'Uploading credentials...' : 'Click to Upload JSON Key File'}
                    </span>
                    <span className="text-[9px] text-slate-400 font-sans">Overwrites backend service_account.json to bind active calendar connection.</span>
                  </div>
                </div>

              </div>
            </div>

            {/* Services Configuration Card */}
            <div className="bg-white rounded-xl shadow-sm border border-slate-200 overflow-hidden flex flex-col">
              <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center gap-2">
                <Sliders className="w-5 h-5 text-indigo-650" />
                <h2 className="font-bold text-slate-800 text-sm">Services & Booking Configuration</h2>
              </div>
              
              <div className="p-5 flex flex-col gap-6 font-sans">
                
                {/* SMS Template Section */}
                <div className="flex flex-col gap-2.5 pb-5 border-b border-slate-150">
                  <div>
                    <h3 className="font-bold text-slate-800 text-xs flex items-center gap-1.5">
                      <FileText className="w-3.5 h-3.5 text-indigo-650" />
                      Booking SMS Confirmation Template
                    </h3>
                    <p className="text-[10px] text-slate-500 mt-0.5">Customize the SMS sent after scheduling. Booking tokens: <code>{`{name}`}</code>, <code>{`{service}`}</code>, and <code>{`{time}`}</code>. Saved business variable tokens also work, such as <code>{`{street_address}`}</code>.</p>
                  </div>
                  <div className="flex gap-2">
                    <textarea
                      value={smsTemplate}
                      onChange={(e) => setSmsTemplate(e.target.value)}
                      rows={2}
                      className="flex-1 font-mono text-[11px] p-2.5 rounded-lg border border-slate-300 focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                    <button
                      onClick={handleSaveSmsTemplate}
                      disabled={savingSmsTemplate}
                      className="bg-indigo-600 hover:bg-indigo-700 text-white font-bold text-xs px-4 py-2 rounded-lg shadow-sm transition-all cursor-pointer h-fit self-end shrink-0"
                    >
                      {savingSmsTemplate ? 'Saving...' : 'Save Template'}
                    </button>
                  </div>
                </div>

                {/* Services List Section */}
                <div className="flex flex-col gap-4">
                  <div>
                    <h3 className="font-bold text-slate-800 text-xs">Manage Service Items</h3>
                    <p className="text-[10px] text-slate-500 mt-0.5">Define services selectable in your multi-step booking form.</p>
                  </div>

                  {/* List of services */}
                  <div className="border border-slate-200 rounded-lg overflow-hidden bg-slate-50 divide-y divide-slate-200 max-h-[250px] overflow-y-auto">
                    {services.length === 0 ? (
                      <div className="py-8 text-center text-xs text-slate-400">
                        No services configured. Use the form below to add.
                      </div>
                    ) : (
                      services.map((srv, index) => {
                        if (editingServiceId === srv.id) {
                          return (
                            <form key={srv.id} onSubmit={handleUpdateService} className="p-3 bg-indigo-50/50 flex flex-col gap-2.5 font-sans">
                              <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
                                <input
                                  type="text"
                                  value={editServiceName}
                                  onChange={(e) => setEditServiceName(e.target.value)}
                                  placeholder="Service Name"
                                  className="text-xs border border-slate-350 rounded p-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white md:col-span-2"
                                  required
                                />
                                <input
                                  type="number"
                                  value={editServicePrice}
                                  onChange={(e) => setEditServicePrice(parseInt(e.target.value) || 0)}
                                  placeholder="Price ($)"
                                  className="text-xs border border-slate-355 rounded p-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white"
                                  min="1"
                                  required
                                />
                                <input
                                  type="number"
                                  value={editServiceDuration}
                                  onChange={(e) => setEditServiceDuration(parseInt(e.target.value) || 0)}
                                  placeholder="Duration (mins)"
                                  className="text-xs border border-slate-355 rounded p-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white"
                                  min="5"
                                  required
                                />
                              </div>
                              <textarea
                                value={editServiceDesc}
                                onChange={(e) => setEditServiceDesc(e.target.value)}
                                placeholder="Service description..."
                                rows={2}
                                className="text-xs border border-slate-350 rounded p-1.5 focus:outline-none focus:ring-1 focus:ring-indigo-500 bg-white"
                              />
                              <div className="flex items-center justify-between">
                                <label className="flex items-center gap-2 cursor-pointer select-none">
                                  <button
                                    type="button"
                                    onClick={() => setEditServiceShowDuration(p => !p)}
                                    className={`relative w-8 h-4 rounded-full transition-colors cursor-pointer ${editServiceShowDuration ? 'bg-indigo-600' : 'bg-slate-300'}`}
                                  >
                                    <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white shadow-sm transition-transform ${editServiceShowDuration ? 'translate-x-4' : 'translate-x-0'}`} />
                                  </button>
                                  <span className="text-[10px] text-slate-650 font-bold select-none">
                                    {editServiceShowDuration ? 'Show duration on booking form' : 'Hide duration on booking form'}
                                  </span>
                                </label>
                                <div className="flex items-center gap-2">
                                  <button
                                    type="button"
                                    onClick={cancelEditService}
                                    className="px-2.5 py-1 text-[10px] font-bold border border-slate-300 rounded text-slate-600 hover:bg-slate-100 transition-all cursor-pointer bg-white"
                                  >
                                    Cancel
                                  </button>
                                  <button
                                    type="submit"
                                    className="px-2.5 py-1 text-[10px] font-bold bg-indigo-650 hover:bg-indigo-700 text-white rounded shadow-sm transition-all cursor-pointer"
                                  >
                                    Update
                                  </button>
                                </div>
                              </div>
                            </form>
                          );
                        }
                        return (
                          <div
                            key={srv.id}
                            draggable={editingServiceId !== srv.id}
                            onDragStart={(e) => handleDragStart(e, index)}
                            onDragOver={(e) => handleDragOver(e, index)}
                            onDragEnd={handleDragEnd}
                            className={`p-3 flex justify-between items-start gap-4 hover:bg-slate-100/50 transition-colors select-none cursor-grab active:cursor-grabbing ${
                              draggedIndex === index ? 'opacity-40 bg-indigo-50/30 border-y border-dashed border-indigo-200' : ''
                            }`}
                          >
                            <div className="flex items-start gap-2.5">
                              <GripVertical className="w-4 h-4 text-slate-400 mt-0.5 shrink-0" />
                              <div className="flex flex-col gap-1">
                                <span className="font-bold text-slate-800 text-xs flex items-center gap-1.5">
                                  {srv.name}
                                  {srv.showDuration !== false && (
                                    <span className="text-[9px] bg-slate-200 text-slate-650 font-bold px-1.5 py-0.5 rounded">
                                      {srv.duration} mins
                                    </span>
                                  )}
                                  {srv.showDuration === false && (
                                    <span className="text-[9px] bg-amber-100 text-amber-700 font-bold px-1.5 py-0.5 rounded border border-amber-200">
                                      duration hidden
                                    </span>
                                  )}
                                </span>
                                <span className="text-[10px] text-slate-500 line-clamp-2">{srv.description}</span>
                              </div>
                            </div>
                            <div className="flex items-center gap-3 shrink-0">
                              <span className="font-bold text-slate-800 text-xs flex items-center">
                                <DollarSign className="w-3.5 h-3.5 text-slate-400 stroke-[2.5]" />
                                {srv.price}
                              </span>
                              <div className="flex items-center gap-1.5">
                                <button
                                  onClick={() => startEditService(srv)}
                                  className="p-1 hover:bg-indigo-100 rounded text-indigo-600 transition-colors cursor-pointer"
                                  title="Edit service"
                                >
                                  <Edit className="w-3.5 h-3.5" />
                                </button>
                                <button
                                  onClick={() => handleDeleteService(srv.id)}
                                  className="p-1 hover:bg-rose-100 rounded text-rose-600 transition-colors cursor-pointer"
                                  title="Delete service"
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            </div>
                          </div>
                        );
                      })
                    )}
                  </div>

                  {/* Add Service form */}
                  <form onSubmit={handleAddService} className="p-3.5 bg-slate-50 border border-slate-200 rounded-lg flex flex-col gap-3">
                    <h4 className="font-bold text-slate-700 text-xs flex items-center gap-1">
                      <Plus className="w-3.5 h-3.5 text-slate-400" />
                      Add New Service
                    </h4>
                    <div className="grid grid-cols-1 md:grid-cols-4 gap-2">
                      <input
                        type="text"
                        value={newServiceName}
                        onChange={(e) => setNewServiceName(e.target.value)}
                        placeholder="Service Name (e.g. Full Massage)"
                        className="text-xs border border-slate-300 rounded p-2 focus:outline-none focus:ring-1 focus:ring-indigo-500 md:col-span-2"
                        required
                      />
                      <input
                        type="number"
                        value={newServicePrice}
                        onChange={(e) => setNewServicePrice(parseInt(e.target.value) || 0)}
                        placeholder="Price ($)"
                        className="text-xs border border-slate-300 rounded p-2 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                        min="1"
                        required
                      />
                      <input
                        type="number"
                        value={newServiceDuration}
                        onChange={(e) => setNewServiceDuration(parseInt(e.target.value) || 0)}
                        placeholder="Duration (mins)"
                        className="text-xs border border-slate-300 rounded p-2 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                        min="5"
                        required
                      />
                    </div>
                    <textarea
                      value={newServiceDesc}
                      onChange={(e) => setNewServiceDesc(e.target.value)}
                      placeholder="Service description detailing what's included..."
                      rows={2}
                      className="text-xs border border-slate-300 rounded p-2 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    />
                    <div className="flex items-center justify-between">
                      {/* Show/hide duration toggle */}
                      <label className="flex items-center gap-2 cursor-pointer select-none">
                        <button
                          type="button"
                          onClick={() => setNewServiceShowDuration(p => !p)}
                          className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer ${newServiceShowDuration ? 'bg-indigo-600' : 'bg-slate-300'}`}
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

                    {/* Option 1: Dynamic Auto-Resize Script (Recommended) */}
                    <div className="p-4 bg-slate-50 border border-slate-200 rounded-xl flex flex-col gap-2">
                      <div className="flex justify-between items-center">
                        <span className="font-bold text-slate-700 text-xs flex items-center gap-1.5">
                          <Calendar className="w-3.5 h-3.5 text-indigo-500" /> Option A: Auto-Resizing Embed (Recommended)
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
                        Uses <strong>widget.js</strong> to dynamically adjust the height of the booking form on your website so it loads seamlessly with no double scrollbars.
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
          <div className="p-4 border-b border-slate-200 bg-slate-50/50 flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <MessageSquare className="w-5 h-5 text-emerald-600" />
              <div>
                <h2 className="font-bold text-slate-800 text-sm">First Contact Auto-Responder</h2>
                <p className="text-[10px] text-slate-500">Send one fixed reply after a phone number has been quiet for the chosen number of days.</p>
              </div>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={firstContactConfig.enabled}
              onClick={() => setFirstContactConfig(current => ({ ...current, enabled: !current.enabled }))}
              className={`relative h-6 w-11 shrink-0 rounded-full border-none p-0 transition-colors cursor-pointer ${
                firstContactConfig.enabled ? 'bg-emerald-500' : 'bg-slate-300'
              }`}
            >
              <span className={`absolute left-0.5 top-0.5 h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
                firstContactConfig.enabled ? 'translate-x-5' : 'translate-x-0'
              }`} />
            </button>
          </div>

          <div className="p-5 flex flex-col gap-4 font-sans">
            <div className="grid grid-cols-1 sm:grid-cols-[180px_180px_1fr] gap-4">
              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Quiet Period</label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={1}
                    max={3650}
                    value={firstContactConfig.cooldownDays}
                    onChange={(e) => setFirstContactConfig(current => ({
                      ...current,
                      cooldownDays: Number(e.target.value)
                    }))}
                    className="w-24 text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-slate-50"
                  />
                  <span className="text-xs font-semibold text-slate-600">days</span>
                </div>
                <p className="text-[10px] text-slate-400">The same number can receive this reply again only after this many days without messaging.</p>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Response Delay</label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={0}
                    max={3600}
                    step={1}
                    value={firstContactConfig.delaySeconds}
                    onChange={(e) => setFirstContactConfig(current => ({
                      ...current,
                      delaySeconds: Number(e.target.value)
                    }))}
                    className="w-24 text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-slate-50"
                  />
                  <span className="text-xs font-semibold text-slate-600">seconds</span>
                </div>
                <p className="text-[10px] text-slate-400">Wait this long after the first message before sending the fixed reply.</p>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-[10px] font-bold text-slate-600 uppercase tracking-wider">Fixed Reply Message</label>
                <textarea
                  value={firstContactConfig.message}
                  onChange={(e) => setFirstContactConfig(current => ({ ...current, message: e.target.value }))}
                  rows={4}
                  maxLength={1600}
                  placeholder="Type the exact reply to send on first contact..."
                  className="text-xs border border-slate-300 rounded-lg p-2.5 focus:outline-none focus:ring-2 focus:ring-emerald-500 bg-slate-50"
                />
                <p className="text-[10px] text-slate-400">This reply is sent instead of an AI reply for that first message, preventing duplicate responses.</p>
              </div>
            </div>

            <div className="flex items-center justify-between gap-3 pt-2 border-t border-slate-150">
              <span className={`text-[10px] font-extrabold px-2.5 py-1 rounded-full ${
                firstContactConfig.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-500'
              }`}>
                {firstContactConfig.enabled ? 'Enabled' : 'Off'}
              </span>
              <button
                type="button"
                onClick={handleSaveFirstContact}
                disabled={savingFirstContact}
                className="bg-emerald-600 hover:bg-emerald-700 disabled:opacity-40 text-white font-bold text-xs px-4 py-2.5 rounded-lg shadow-sm transition-all cursor-pointer border border-transparent"
              >
                {savingFirstContact ? 'Saving...' : 'Save First Contact Reply'}
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
