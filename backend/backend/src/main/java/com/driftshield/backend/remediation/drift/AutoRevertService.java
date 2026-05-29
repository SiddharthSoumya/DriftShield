package com.driftshield.backend.remediation.drift;


import com.driftshield.backend.entity.drift.DriftEvent;
import com.driftshield.backend.repository.drift.DriftEventRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
@Slf4j
public class AutoRevertService {

    private final ConfigRestoreService configRestoreService;
    private final DriftEventRepository driftEventRepository;

    public void autoRestoreInfrastructure(
            Map<String,Object> baselineConfig,
            List<DriftEvent> driftEvents
    ){
        configRestoreService.restoreRuntimeConfig(baselineConfig);

        for (DriftEvent event: driftEvents){
            event.setReverted(true);
            driftEventRepository.save(event);
        }

        log.info(
                "Infrastructure auto-remediated successfully"
        );
    }
}
