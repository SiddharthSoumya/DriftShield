package com.driftshield.backend.service.alert;

import com.driftshield.backend.dto.request.AlertRequest;
import com.driftshield.backend.dto.response.AlertResponse;
import com.driftshield.backend.entity.alert.Alert;
import com.driftshield.backend.repository.alert.AlertRepository;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class AlertService {
    public final AlertRepository alertRepository;

    public AlertResponse createAlert(AlertRequest request){
        Alert alert =Alert.builder()
                .type(request.getType())
                .severity(request.getSeverity())
                .message(request.getMessage())
                .createdAt(LocalDateTime.now())
                .build();
        Alert savedAlert =alertRepository.save(alert);
        return mapToResponse(savedAlert);
    }

    public List<AlertResponse> getAllAlerts(){
        return alertRepository.findAll()
                .stream()
                .map(this::mapToResponse)
                .toList();
    }

    private AlertResponse mapToResponse(Alert alert){
        return AlertResponse.builder()
                .id(alert.getId())
                .type(alert.getType())
                .severity(alert.getSeverity())
                .message(alert.getMessage())
                .createdAt(alert.getCreatedAt())
                .build();
    }
}
