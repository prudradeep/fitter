window.DrTransitionSettings = {
  storageKeys: {
    session: "dr_transition_session_id",
    inputStatePrefix: "dr_transition_input_state_",
    voiceEnabled: "dr_transition_voice_enabled",
    voicePreference: "dr_transition_voice_preference",
    voiceLanguage: "dr_transition_voice_language",
    voiceRate: "dr_transition_voice_rate",
    voiceVolume: "dr_transition_voice_volume",
    typingEffect: "dr_transition_typing_effect_enabled",
    autoConversation: "dr_transition_auto_conversation_enabled",
    validationMode: "dr_transition_validation_mode",
    crowdSourcing: "dr_transition_crowd_sourcing_enabled",
    promptSource: "dr_transition_prompt_source",
    panelWidth: "dr_transition_visual_panel_width",
  },
  assets: {
    teacherAvatarPath: "/static/img/teacher.png",
  },
  chat: {
    collapsibleMessageWordLimit: 100,
    autoConversationTurnLimit: 80,
  },
  stagePanel: {
    defaultVisualPanelPercent: 43,
    minPercent: 30,
    maxPercent: 62,
  },
};
