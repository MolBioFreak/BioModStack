/** Native FA controls shared by ordinary and inline owners. */
class FampnnNativeControls {
    static String arguments(params) {
    def typedNativeMapping = [
        'fampnn_seed': 'seed',
        'fampnn_presort_by_length': 'presort_by_length',
        'fampnn_timestep_mode': 'timestep_schedule.mode',
        'fampnn_timestep_start': 'timestep_schedule.t_start',
        'fampnn_timestep_end': 'timestep_schedule.t_end',
        'fampnn_scn_num_steps': 'scn_diffusion.num_steps',
        'fampnn_scn_timestep_mode': 'scn_diffusion.timestep_schedule.mode',
        'fampnn_scn_timestep_start': 'scn_diffusion.timestep_schedule.t_start',
        'fampnn_scn_timestep_end': 'scn_diffusion.timestep_schedule.t_end',
        'fampnn_scn_step_scale': 'scn_diffusion.step_scale',
        'fampnn_scn_s_churn': 'scn_diffusion.churn_cfg.s_churn',
        'fampnn_scn_s_noise': 'scn_diffusion.churn_cfg.s_noise',
        'fampnn_scn_s_t_min': 'scn_diffusion.churn_cfg.s_t_min',
        'fampnn_scn_s_t_max': 'scn_diffusion.churn_cfg.s_t_max',
    ]
    return typedNativeMapping.findAll { key, nativeKey -> params.get(key) != null }.collect { key, nativeKey ->
        "'" + (nativeKey + '=' + params.get(key).toString()).replace("'", "'\"'\"'") + "'"
    }.join(' ')
    }
}
